"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import os

from osbs.build.build_request import BuildRequest
from osbs.build.user_params import BuildUserParams
from osbs.exceptions import OsbsValidationException
from osbs.constants import SECRETS_PATH
from osbs.utils import git_repo_humanish_part_from_uri

logger = logging.getLogger(__name__)

_CM_SECRETS = ['required_secrets', 'worker_token_secrets']


class BuildRequestV2(BuildRequest):
    """
    Wraps logic for creating build inputs
    """

    def __init__(self, build_json_store, customize_conf=None):
        """
        :param build_json_store: str, path to directory with JSON build files
        :param customize_conf: str, path to customize configuration JSON
        """
        super(BuildRequestV2, self).__init__(build_json_store=build_json_store,
                                             customize_conf=customize_conf)
        self.spec = None
        self.user_params = BuildUserParams(build_json_store)

    # Override
    def set_params(self, **kwargs):
        """
        set parameters in the user parameters

        these parameters are accepted:
        :param git_uri: str, uri of the git repository for the source
        :param git_ref: str, commit ID of the branch to be pulled
        :param git_branch: str, branch name of the branch to be pulled
        :param base_image: str, name of the parent image
        :param name_label: str, label of the parent image
        :param user: str, name of the user requesting the build
        :param component: str, name of the component
        :param release: str,
        :param build_image: str,
        :param build_imagestream: str,
        :param build_from: str,
        :param build_type: str, orchestrator or worker
        :param platforms: list of str, platforms to build on
        :param platform: str, platform
        :param koji_target: str, koji tag with packages used to build the image
        :param koji_task_id: str, koji ID
        :param koji_parent_build: str,
        :param flatpak: if we should build a Flatpak OCI Image
        :param flatpak_base_image: str, name of the Flatpack OCI Image
        :param reactor_config_map: str, name of the config map containing the reactor environment
        :param yum_repourls: list of str, uris of the yum repos to pull from
        :param signing_intent: bool, True to sign the resulting image
        :param compose_ids: list of str,
        :param filesystem_koji_task_id: int, Koji Task that created the base filesystem
        :param platform_node_selector: dict, a nodeselector for a user_paramsific platform
        :param scratch_build_node_selector: dict, a nodeselector for scratch builds
        :param explicit_build_node_selector: dict, a nodeselector for explicit builds
        :param auto_build_node_selector: dict, a nodeselector for auto builds
        :param isolated_build_node_selector: dict, a nodeselector for isolated builds
        :param additional_tag_data: dict, str of dir path and filename of additional tags
                                    and set of str of tags
        """

        # Here we cater to the koji "scratch" build type, this will disable
        # all plugins that might cause importing of data to koji
        self.scratch = kwargs.get('scratch')
        # When true, it indicates build was automatically started by
        # OpenShift via a trigger, for instance ImageChangeTrigger
        self.is_auto = kwargs.pop('is_auto', False)
        # An isolated build is meant to patch a certain release and not
        # update transient tags in container registry
        self.isolated = kwargs.get('isolated')

        self.validate_build_variation()

        self.base_image = kwargs.get('base_image')
        self.platform_node_selector = kwargs.get('platform_node_selector', {})
        self.scratch_build_node_selector = kwargs.get('scratch_build_node_selector', {})
        self.explicit_build_node_selector = kwargs.get('explicit_build_node_selector', {})
        self.auto_build_node_selector = kwargs.get('auto_build_node_selector', {})
        self.isolated_build_node_selector = kwargs.get('isolated_build_node_selector', {})

        logger.debug("setting params '%s' for %s", kwargs, self.user_params)
        self.user_params.set_params(**kwargs)

    # Override
    @property
    def inner_template(self):
        raise RuntimeError('inner_template not supported in BuildRequestV2')

    # Override
    @property
    def customize_conf(self):
        raise RuntimeError('customize_conf not supported in BuildRequestV2')

    # Override
    @property
    def dj(self):
        raise RuntimeError('DockJson not supported in BuildRequestV2')

    def set_reactor_config_map(self, api):
        if not self.user_params.reactor_config_map.value:
            return
        super(BuildRequestV2, self).set_reactor_config_map(self.user_params.reactor_config_map)
        cm_response = api.get_config_map(self.user_params.reactor_config_map.value)
        custom = self.template['spec']['strategy']['customStrategy']
        custom.setdefault('secrets', [])
        existing = [secret_mount['secretSource']['name'] for secret_mount in custom['secrets']]
        for key in _CM_SECRETS:
            secrets = cm_response.get_data_by_key(key)
            for secret in secrets:
                if secret in existing:
                    continue
                else:
                    custom['secrets'].append({
                        'secretSource': {
                            'name': secret,
                        },
                        'mountPath': os.path.join(SECRETS_PATH, secret),
                    })

    def adjust_for_scratch(self):
        """
        Scratch builds must not affect subsequent builds,
        and should not be imported into Koji.
        """
        if self.scratch:
            self.template['spec'].pop('triggers', None)
            self.set_label('scratch', 'true')

    def render_node_selectors(self):
        # for worker builds set nodeselectors
        if self.user_params.platforms.value is None:
            # auto or explicit build selector
            if self.is_auto:
                self.template['spec']['nodeSelector'] = self.auto_build_node_selector
            # scratch build nodeselector
            elif self.scratch:
                self.template['spec']['nodeSelector'] = self.scratch_build_node_selector
            # isolated build nodeselector
            elif self.isolated:
                self.template['spec']['nodeSelector'] = self.isolated_build_node_selector
            # explicit build nodeselector
            else:
                self.template['spec']['nodeSelector'] = self.explicit_build_node_selector

            # platform nodeselector
            if self.platform_node_selector:
                self.template['spec']['nodeSelector'].update(self.platform_node_selector)

    def render(self, api=None, validate=True):
        # the api is required for BuildRequestV2
        # can't check that its an OSBS object because of the circular import
        if not api:
            raise OsbsValidationException

        # Validate BuildUserParams
        if validate:
            self.user_params.validate()

        self.render_name(self.user_params.name, self.user_params.image_tag,
                         self.user_params.platform)
        self.render_resource_limits()

        self.template['spec']['source']['git']['uri'] = self.user_params.git_uri.value
        self.template['spec']['source']['git']['ref'] = self.user_params.git_ref.value

        self.template['spec']['output']['to']['name'] = self.user_params.image_tag.value

        if self.has_ist_trigger():
            imagechange = self.template['spec']['triggers'][0]['imageChange']
            imagechange['from']['name'] = self.user_params.trigger_imagestreamtag.value

        custom_strategy = self.template['spec']['strategy']['customStrategy']
        if self.user_params.build_imagestream.value:
            custom_strategy['from']['kind'] = 'ImageStreamTag'
            custom_strategy['from']['name'] = self.user_params.build_imagestream.value
        else:
            custom_strategy['from']['name'] = self.user_params.build_image.value

        # Set git-repo-name label
        # NOTE: Since only the repo name is used, a forked repos will have
        # the same git-repo-name tag. This is a known limitation. If this
        # use case must be handled properly, the git URI must be taken into
        # account.
        repo_name = git_repo_humanish_part_from_uri(self.user_params.git_uri.value)
        self.set_label('git-repo-name', repo_name)
        self.set_label('git-branch', self.user_params.git_branch.value)
        koji_task_id = self.user_params.koji_task_id.value
        if koji_task_id is not None:
            self.set_label('koji-task-id', str(koji_task_id))

        # Set template.spec.strategy.customStrategy.env[] USER_PARAMS
        # Set required_secrets based on reactor_config
        # Set worker_token_secrets based on reactor_config, if any
        self.set_reactor_config_map(api)

        # Adjust triggers for custom base image
        if self.template['spec'].get('triggers', []) and self.is_custom_base_image():
            del self.template['spec']['triggers']

        self.adjust_for_repo_info()
        self.adjust_for_scratch()
        self.adjust_for_isolated(self.user_params.release)
        self.render_node_selectors()

        # Log build json
        # Return build json
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json
