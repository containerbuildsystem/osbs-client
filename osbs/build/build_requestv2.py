"""
Copyright (c) 2018, 2019 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import abc
import json
import logging
import re
import os
import yaml
from pkg_resources import parse_version
import six

from osbs.build.user_params import (
    BuildUserParams,
    SourceContainerUserParams,
)
from osbs.constants import (SECRETS_PATH, DEFAULT_OUTER_TEMPLATE,
                            DEFAULT_SOURCES_OUTER_TEMPLATE,
                            DEFAULT_CUSTOMIZE_CONF, BUILD_TYPE_ORCHESTRATOR,
                            BUILD_TYPE_WORKER, ISOLATED_RELEASE_FORMAT)
from osbs.exceptions import OsbsException, OsbsValidationException
from osbs.utils.labels import Labels
from osbs.utils import git_repo_humanish_part_from_uri, sanitize_strings_for_openshift

logger = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class BaseBuildRequest(object):
    """
    Abstract class for all build requests

    Subclasses must:
      * implement `render` method which returns builds input
      * initialize proper user_params class
    """
    def __init__(self, osbs_api, outer_template, user_params, build_json_store=None):
        self._openshift_required_version = parse_version('3.6.0')
        self._outer_template_path = outer_template
        self._resource_limits = None
        self._template = None

        self.organization = None
        self.osbs_api = osbs_api
        self.source_registry = None
        self.user_params = user_params
        if user_params and user_params.build_json_dir.value:
            self._build_json_store = user_params.build_json_dir.value
        else:
            self._build_json_store = build_json_store

    def set_params(self, user_params):
        self.user_params = user_params
        if self._build_json_store and not user_params.build_json_dir.value:
            self.user_params.build_json_dir.value = self._build_json_store

    def delete_atomic_reactor_placeholder(self):
        """Delete the ATOMIC_REACTOR_PLUGINS placeholder"""
        custom_strategy = self.template['spec']['strategy']['customStrategy']
        for (index, env) in enumerate(custom_strategy['env']):
            if env['name'] == 'ATOMIC_REACTOR_PLUGINS':
                del custom_strategy['env'][index]
                break

    @abc.abstractmethod
    def render(self, validate=True):
        # the api is required for build requests
        # can't check that its an OSBS object because of the circular import
        if not self.osbs_api:
            raise OsbsValidationException("OSBS API is not specified")

        # Validate BuildUserParams
        if validate:
            self.user_params.validate()

        self.render_name()
        self.render_output_name()
        self.render_custom_strategy()
        self.render_resource_limits()
        self.adjust_for_scratch()
        self.set_reactor_config()
        self.render_user_params()
        self.delete_atomic_reactor_placeholder()

        # Set required_secrets based on reactor_config
        # Set worker_token_secrets based on reactor_config, if any
        data = self.get_reactor_config_data()
        self.set_data_from_reactor_config(data)

        koji_task_id = self.user_params.koji_task_id.value
        if koji_task_id is not None:
            self.set_label('koji-task-id', str(koji_task_id))

        return self.template

    def render_custom_strategy(self):
        """Render data about buildroot used for custom strategy"""
        custom_strategy = self.template['spec']['strategy']['customStrategy']
        if self.user_params.buildroot_is_imagestream.value:
            custom_strategy['from']['kind'] = 'ImageStreamTag'
        custom_strategy['from']['name'] = self.user_params.build_image.value

    def render_name(self):
        """Sets the Build/BuildConfig object name"""
        name = self.user_params.name.value
        platform = self.user_params.platform.value

        if self.user_params.scratch.value or self.user_params.isolated.value:
            name = self.user_params.image_tag.value
            # Platform name may contain characters not allowed by OpenShift.
            if platform:
                platform_suffix = '-{}'.format(platform)
                if name.endswith(platform_suffix):
                    name = name[:-len(platform_suffix)]

            _, salt, timestamp = name.rsplit('-', 2)

            if self.user_params.scratch.value:
                name = 'scratch-{}-{}'.format(salt, timestamp)
            elif self.user_params.isolated.value:
                name = 'isolated-{}-{}'.format(salt, timestamp)

        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = name

    def render_output_name(self):
        self.template['spec']['output']['to']['name'] = self.user_params.image_tag.value

    def render_resource_limits(self):
        if self._resource_limits is not None:
            resources = self.template['spec'].get('resources', {})
            limits = resources.get('limits', {})
            limits.update(self._resource_limits)
            resources['limits'] = limits
            self.template['spec']['resources'] = resources

    def render_user_params(self):
        custom_strategy = self.template['spec']['strategy']['customStrategy']
        # Set our environment variables
        custom_strategy['env'].append({
            'name': 'USER_PARAMS',
            'value': self.user_params.to_json(),
        })

    def adjust_for_scratch(self):
        """
        Scratch builds must not affect subsequent builds,
        and should not be imported into Koji.
        """
        if self.user_params.scratch.value:
            self.template['spec'].pop('triggers', None)
            self.set_label('scratch', 'true')

    def set_deadline(self, deadline_hours):

        if deadline_hours > 0:
            deadline_seconds = deadline_hours * 3600
            self.template['spec']['completionDeadlineSeconds'] = deadline_seconds
            logger.info("setting completion_deadline to %s hours (%s seconds)", deadline_hours,
                        deadline_seconds)

    def set_label(self, name, value):
        if not value:
            value = ''
        self.template['metadata'].setdefault('labels', {})
        value = sanitize_strings_for_openshift(value)
        self.template['metadata']['labels'][name] = value

    def set_openshift_required_version(self, openshift_required_version):
        if openshift_required_version is not None:
            self._openshift_required_version = openshift_required_version

    def set_reactor_config(self):
        reactor_config_override = self.user_params.reactor_config_override.value
        reactor_config_map = self.user_params.reactor_config_map.value

        if not reactor_config_map and not reactor_config_override:
            return
        custom = self.template['spec']['strategy']['customStrategy']

        if reactor_config_override:
            reactor_config = {
                'name': 'REACTOR_CONFIG',
                'value': yaml.safe_dump(reactor_config_override)
            }
        elif reactor_config_map:
            reactor_config = {
                'name': 'REACTOR_CONFIG',
                'valueFrom': {
                    'configMapKeyRef': {
                        'name': reactor_config_map,
                        'key': 'config.yaml'
                    }
                }
            }

        custom['env'].append(reactor_config)

    def set_resource_limits(self, cpu=None, memory=None, storage=None):
        if self._resource_limits is None:
            self._resource_limits = {}

        if cpu is not None:
            self._resource_limits['cpu'] = cpu

        if memory is not None:
            self._resource_limits['memory'] = memory

        if storage is not None:
            self._resource_limits['storage'] = storage

    @property
    def template(self):
        if self._template is None:
            path = os.path.join(self.user_params.build_json_dir.value, self._outer_template_path)
            logger.debug("loading template from path %s", path)
            try:
                with open(path, "r") as fp:
                    self._template = json.load(fp)
            except (IOError, OSError) as ex:
                raise OsbsException("Can't open template '%s': %s" %
                                    (path, repr(ex)))
        return self._template

    def get_reactor_config_data(self):
        """
        Return atomic reactor configuration

        :rval: dict
        :return: atomic-reactor configuration
        """
        reactor_config_override = self.user_params.reactor_config_override.value
        reactor_config_map = self.user_params.reactor_config_map.value
        data = {}

        if reactor_config_override:
            data = reactor_config_override
        elif reactor_config_map:
            config_map = self.osbs_api.get_config_map(reactor_config_map)
            data = config_map.get_data_by_key('config.yaml')
        return data

    def _set_required_secrets(self, required_secrets):
        """
        Sets required secrets
        """
        if not required_secrets:
            return

        secrets = self.template['spec']['strategy']['customStrategy'].setdefault('secrets', [])
        existing = set(secret_mount['secretSource']['name'] for secret_mount in secrets)
        required_secrets = set(required_secrets)

        already_set = required_secrets.intersection(existing)
        if already_set:
            logger.debug("secrets %s are already set", already_set)

        for secret in required_secrets - existing:
            secret_path = os.path.join(SECRETS_PATH, secret)
            logger.info("Configuring %s secret at %s", secret, secret_path)

            secrets.append({
                'secretSource': {
                    'name': secret,
                },
                'mountPath': secret_path,
            })

    def set_required_secrets(self, reactor_config_data):
        """
        Sets required secrets into build config
        """
        req_secrets_key = 'required_secrets'
        required_secrets = reactor_config_data.get(req_secrets_key, [])
        self._set_required_secrets(required_secrets)

    def set_source_registry(self, reactor_config_data):
        """
        Sets source_registry value
        """
        source_registry_key = 'source_registry'
        if source_registry_key in reactor_config_data:
            self.source_registry = reactor_config_data[source_registry_key]

    def set_registry_organization(self, reactor_config_data):
        """
        Sets registry organization
        """
        registry_organization_key = 'registries_organization'

        if registry_organization_key in reactor_config_data:
            self.organization = reactor_config_data[registry_organization_key]

    def set_data_from_reactor_config(self, reactor_config_data):
        """
        Sets data from reactor config
        """
        self.set_source_registry(reactor_config_data)
        self.set_registry_organization(reactor_config_data)
        self.set_required_secrets(reactor_config_data)


class BuildRequestV2(BaseBuildRequest):
    """
    Wraps logic for creating build inputs
    """
    def __init__(self, osbs_api, outer_template=None, customize_conf=None, user_params=None,
                 build_json_store=None):
        """
        :param build_json_store: str, path to directory with JSON build files
        :param outer_template: str, path to outer template JSON
        :param customize_conf: str, path to customize configuration JSON
        """
        if user_params:
            assert isinstance(user_params, BuildUserParams)
        else:
            user_params = BuildUserParams()
        super(BuildRequestV2, self).__init__(
            osbs_api,
            outer_template=outer_template or DEFAULT_OUTER_TEMPLATE,
            user_params=user_params,
            build_json_store=build_json_store,
        )

        self._customize_conf_path = customize_conf or DEFAULT_CUSTOMIZE_CONF
        self.build_json = None       # rendered template

    # Override
    @property
    def customize_conf(self):
        raise RuntimeError('customize_conf not supported in BuildRequestV2')

    def set_params(self, user_params):
        super(BuildRequestV2, self).set_params(user_params)
        assert isinstance(user_params, BuildUserParams)

    @property
    def isolated(self):
        return self.user_params.isolated.value

    @property
    def scratch(self):
        return self.user_params.scratch.value

    @property
    def skip_build(self):
        return self.user_params.skip_build.value

    @property
    def triggered_after_koji_task(self):
        return self.user_params.triggered_after_koji_task.value

    @property
    def base_image(self):
        return self.user_params.base_image.value

    # Override
    @property
    def trigger_imagestreamtag(self):
        return self.user_params.trigger_imagestreamtag.value

    def _set_flatpak(self, reactor_config_data):
        flatpak_key = 'flatpak'
        flatpak_base_image_key = 'base_image'

        if self.user_params.flatpak.value and not self.user_params.base_image.value:
            flatpack_base_image = (
                reactor_config_data.get(flatpak_key, {}).get(flatpak_base_image_key, None)
            )
            if flatpack_base_image:
                self.user_params.set_base_image(flatpack_base_image)
            else:
                raise OsbsValidationException(
                    "Flatpak base_image must be be set in container.yaml or reactor config")

    def set_required_secrets(self, reactor_config_data):
        """
        Sets required secrets into build config
        """
        req_secrets_key = 'required_secrets'
        token_secrets_key = 'worker_token_secrets'
        required_secrets = reactor_config_data.get(req_secrets_key, [])
        token_secrets = reactor_config_data.get(token_secrets_key, [])

        if self.user_params.build_type.value == BUILD_TYPE_ORCHESTRATOR:
            required_secrets += token_secrets
        self._set_required_secrets(required_secrets)

    def set_data_from_reactor_config(self, reactor_config_data):
        """
        Sets data from reactor config
        """
        super(BuildRequestV2, self).set_data_from_reactor_config(reactor_config_data)

        if not reactor_config_data:
            if self.user_params.flatpak.value and not self.user_params.base_image.value:
                raise OsbsValidationException(
                    "Flatpak base_image must be be set in container.yaml or reactor config")
            else:
                return

        self._set_flatpak(reactor_config_data)

    def render(self, validate=True):
        super(BuildRequestV2, self).render(validate=validate)

        self.template['spec']['source']['git']['uri'] = self.user_params.git_uri.value
        self.template['spec']['source']['git']['ref'] = self.user_params.git_ref.value

        if self.has_ist_trigger():
            imagechange = self.template['spec']['triggers'][0]['imageChange']
            imagechange['from']['name'] = self.trigger_imagestreamtag

        # Set git-repo-name and git-full-name labels
        repo_name = git_repo_humanish_part_from_uri(self.user_params.git_uri.value)
        # Use the repo name to differentiate different repos, but include the full url as an
        # optional filter.
        self.set_label('git-repo-name', repo_name)
        self.set_label('git-branch', self.user_params.git_branch.value)
        self.set_label('git-full-repo', self.user_params.git_uri.value)

        koji_task_id = self.user_params.koji_task_id.value
        if koji_task_id is not None:
            # keep also original task for all manual builds with task
            # that way when delegated task for autorebuild will be used
            # we will still keep track of it
            if self.user_params.triggered_after_koji_task.value is None:
                self.set_label('original-koji-task-id', str(koji_task_id))

        # Set template.spec.strategy.customStrategy.env[] USER_PARAMS
        # Adjust triggers for custom base image
        if (self.template['spec'].get('triggers', []) and
                (self.is_custom_base_image() or self.is_from_scratch_image())):
            if self.is_custom_base_image():
                logger.info("removing triggers from request because custom base image")
            elif self.is_from_scratch_image():
                logger.info('removing from request because FROM scratch image')
            del self.template['spec']['triggers']

        self.adjust_for_repo_info()
        self.adjust_for_isolated(self.user_params.release)
        self.render_node_selectors(self.user_params.build_type.value)

        self._set_deadline()

        # Log build json
        # Return build json
        self.build_json = self.template
        logger.debug(self.build_json)
        return self.build_json

    @property
    def build_id(self):
        return self.build_json['metadata']['name']

    def has_ist_trigger(self):
        """Return True if this BuildConfig has ImageStreamTag trigger."""
        triggers = self.template['spec'].get('triggers', [])
        if not triggers:
            return False
        for trigger in triggers:
            if trigger['type'] == 'ImageChange' and \
                    trigger['imageChange']['from']['kind'] == 'ImageStreamTag':
                return True
        return False

    def render_node_selectors(self, build_type):
        # for worker builds set nodeselectors
        if build_type == BUILD_TYPE_WORKER:

            # auto or explicit build selector
            if self.user_params.is_auto.value:
                node_selector = self.user_params.auto_build_node_selector
            # scratch build nodeselector
            elif self.user_params.scratch.value:
                node_selector = self.user_params.scratch_build_node_selector
            # isolated build nodeselector
            elif self.user_params.isolated.value:
                node_selector = self.user_params.isolated_build_node_selector
            # explicit build nodeselector
            else:
                node_selector = self.user_params.explicit_build_node_selector

            platform_ns = self.user_params.platform_node_selector

            # platform nodeselector
            if platform_ns:
                node_selector.update(platform_ns)
            self.template['spec']['nodeSelector'] = node_selector

    def _set_deadline(self):
        if self.user_params.build_type.value == BUILD_TYPE_WORKER:
            deadline_hours = self.user_params.worker_deadline.value
        else:
            deadline_hours = self.user_params.orchestrator_deadline.value

        self.set_deadline(deadline_hours)

    def adjust_for_repo_info(self):
        if not self.user_params.repo_info:
            logger.warning('repo info not set')
            return

        if not self.user_params.repo_info.configuration.is_autorebuild_enabled():
            logger.info('autorebuild is disabled in repo configuration, removing triggers')
            self.template['spec'].pop('triggers', None)

        else:
            labels = self.user_params.repo_info.labels

            add_timestamp = self.user_params.repo_info.configuration.autorebuild.\
                get('add_timestamp_to_release', False)

            if add_timestamp:
                logger.info('add_timestamp_to_release is enabled for autorebuilds,'
                            'skipping release check in dockerfile')
                return

            try:
                labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
            except KeyError:
                # As expected, release label not set in Dockerfile
                pass
            else:
                raise RuntimeError('when autorebuild is enabled in repo configuration, '
                                   '"release" label must not be set in Dockerfile')

    def adjust_for_isolated(self, release):
        if not self.user_params.isolated.value:
            return

        self.template['spec'].pop('triggers', None)

        if not release.value:
            raise OsbsValidationException('The release parameter is required for isolated builds.')

        if not ISOLATED_RELEASE_FORMAT.match(release.value):
            raise OsbsValidationException(
                'For isolated builds, the release value must be in the format: {}'
                .format(ISOLATED_RELEASE_FORMAT.pattern))

        self.set_label('isolated', 'true')
        self.set_label('isolated-release', release.value)

    def is_custom_base_image(self):
        """
        Returns whether or not this is a build from a custom base image
        """
        return bool(re.match('^koji/image-build(:.*)?$',
                             self.user_params.base_image.value or ''))

    def is_from_scratch_image(self):
        """
        Returns whether or not this is a build `FROM scratch`
        """
        return self.user_params.base_image.value == 'scratch'


class SourceBuildRequest(BaseBuildRequest):
    """Build request for source containers"""
    def __init__(self, osbs_api, outer_template=None, user_params=None):
        """
        :param build_json_store: str, path to directory with JSON build files
        :param outer_template: str, path to outer template JSON
        """
        if user_params:
            assert isinstance(user_params, SourceContainerUserParams)
        else:
            user_params = SourceContainerUserParams()
        super(SourceBuildRequest, self).__init__(
            osbs_api,
            outer_template=outer_template or DEFAULT_SOURCES_OUTER_TEMPLATE,
            user_params=user_params,
        )

    def set_params(self, user_params):
        super(SourceBuildRequest, self).set_params(user_params)
        assert isinstance(user_params, SourceContainerUserParams)

    def render(self, validate=True):
        return super(SourceBuildRequest, self).render(validate=validate)

    def render_name(self):
        """Sets the Build/BuildConfig object name

        Source container builds must have unique names, because we are not
        using buildConfigs just regular builds
        """
        name = self.user_params.image_tag.value

        _, salt, timestamp = name.rsplit('-', 2)

        name = 'sources-{}-{}'.format(salt, timestamp)
        if self.user_params.scratch.value:
            name = 'scratch-{}'.format(name)

        # !IMPORTANT! can't be too long: https://github.com/openshift/origin/issues/733
        self.template['metadata']['name'] = name
