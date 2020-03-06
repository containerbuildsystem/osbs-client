"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

from abc import abstractproperty, ABCMeta
import logging
import re
import random
import json

import six

from osbs.constants import (DEFAULT_GIT_REF, REACTOR_CONFIG_ARRANGEMENT_VERSION,
                            DEFAULT_CUSTOMIZE_CONF, RAND_DIGITS,
                            WORKER_MAX_RUNTIME, ORCHESTRATOR_MAX_RUNTIME,
                            USER_PARAMS_KIND_IMAGE_BUILDS,
                            USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS,
                            )
from osbs.exceptions import OsbsValidationException
from osbs.utils import (
    get_imagestreamtag_from_image,
    make_name_from_git,
    utcnow)


logger = logging.getLogger(__name__)

KIND_KEY = 'kind'

# keeps map between kind name and object registered with decorator
# @register_user_params
user_param_kinds = {}


def register_user_params(klass):
    """Decorator for registering classes user params classes"""
    assert issubclass(klass, BuildCommon)
    user_param_kinds[klass.KIND] = klass
    return klass


class BuildParam(object):
    """ One parameter of a spec """

    def __init__(self, name, default=None, allow_none=False):
        self.name = name
        self.allow_none = allow_none
        self._value = default

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, val):
        logger.debug("%s = '%s'", self.name, val)
        self._value = val

    def __repr__(self):
        return "BuildParam(%s='%s')" % (self.name, self.value)


class UserParam(BuildParam):
    """ custom class for "user" parameter with postprocessing """
    name = "user"

    def __init__(self):
        super(UserParam, self).__init__(self.name)


class BuildIDParam(BuildParam):
    """ validate build ID """
    name = "name"

    def __init__(self):
        super(BuildIDParam, self).__init__(self.name)

    @BuildParam.value.setter  # pylint: disable=no-member
    def value(self, val):  # pylint: disable=W0221
        # build ID has to conform to:
        #  * 63 chars at most
        #  * (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?

        if len(val) > 63:
            # component + timestamp > 63
            new_name = val[:63]
            logger.warning("'%s' is too long, changing to '%s'", val, new_name)
            val = new_name

        build_id_re = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
        match = build_id_re.match(val)
        if not match:
            logger.error("'%s' is not valid build ID", val)
            raise OsbsValidationException("Build ID '%s', doesn't match regex '%s'" %
                                          (val, build_id_re))
        BuildParam.value.fset(self, val)  # pylint: disable=no-member


def load_user_params_from_json(user_params_json):
    """Load user params from json into proper object

    :param str user_params_json: json with user params
    :rtype: subclass of BuildCommon
    :return: initialized object with user params
    """
    json_dict = json.loads(user_params_json)
    kind = json_dict.get(KIND_KEY, BuildUserParams.KIND)  # BW comp. default to BuildUserParams
    user_params_class = user_param_kinds[kind]
    user_params = user_params_class()
    user_params.from_json(user_params_json)
    return user_params


@six.add_metaclass(ABCMeta)
class BuildCommon(object):
    """Abstract class for user parameters"""

    @abstractproperty
    def KIND(self):
        return 'DEFINE_KIND_NAME_IN_SUBCLASS'

    def __init__(self, build_json_store=None):
        self.arrangement_version = BuildParam(
            "arrangement_version",
            allow_none=True,
            default=REACTOR_CONFIG_ARRANGEMENT_VERSION)
        # build_from contains the full build_from string, including the source type prefix
        self.build_from = BuildParam('build_from')
        # build_image contains the buildroot name, whether the buildroot is a straight image or an
        # imagestream.  buildroot_is_imagestream indicates what type of buildroot
        self.build_image = BuildParam('build_image')
        self.buildroot_is_imagestream = BuildParam('buildroot_is_imagestream', default=False)
        self.build_json_dir = BuildParam('build_json_dir', default=build_json_store)
        self.kind = BuildParam(KIND_KEY, default=self.KIND)
        self.component = BuildParam('component')
        self.image_tag = BuildParam("image_tag")
        self.koji_target = BuildParam("koji_target", allow_none=True)
        self.koji_task_id = BuildParam('koji_task_id', allow_none=True)
        self.platform = BuildParam("platform", allow_none=True)
        self.orchestrator_deadline = BuildParam('orchestrator_deadline', allow_none=True)
        self.reactor_config_map = BuildParam("reactor_config_map", allow_none=True)
        self.reactor_config_override = BuildParam("reactor_config_override", allow_none=True)
        self.scratch = BuildParam('scratch', allow_none=True)
        self.signing_intent = BuildParam('signing_intent', allow_none=True)
        self.user = UserParam()
        self.worker_deadline = BuildParam('worker_deadline', allow_none=True)

        self.required_params = [
            self.build_json_dir,
            self.koji_target,
            self.user,
        ]
        self.convert_dict = {}

    def attrs_finalizer(self):
        for _, param in self.__dict__.items():
            if isinstance(param, BuildParam):
                # check that every parameter has a unique name
                if param.name in self.convert_dict:
                    raise OsbsValidationException(
                        'Two user params with the same name: {}'.format(param.name))
                self.convert_dict[param.name] = param

    def set_params(self,
                   build_conf=None,
                   build_from=None,
                   component=None,
                   koji_target=None,
                   koji_task_id=None,
                   platform=None,
                   reactor_config_override=None,
                   scratch=None,
                   signing_intent=None,
                   user=None,
                   **kwargs):
        """
        set parameters in the user parameters.

        these parameters are accepted:
        :param base_image: str, name of the parent image
        :param build_conf: BuildConfiguration, the build configuration
        :param component: str, name of the component
        :param koji_parent_build: str,
        :param koji_target: str, koji tag with packages used to build the image
        :param koji_task_id: str, koji ID
        :param koji_upload_dir: str, koji directory where the completed image will be uploaded
        :param platform: str, platform
        :param reactor_config_override: dict, data structure for reactor config to be injected as
                                        an environment variable into a worker build;
                                        when used, reactor_config_map is ignored.
        :param scratch: bool, build as a scratch build
        :param signing_intent: bool, True to sign the resulting image
        :param user: str, name of the user requesting the build

        Please keep the paramater list alphabetized for easier tracking of changes

        the following parameters are pulled from the BuildConfiguration (ie, build_conf)
        :param build_from: str,
        :param orchestrator_deadline: int, orchestrator deadline in hours
        :param reactor_config_map: str, name of the config map containing the reactor environment
        :param worker_deadline: int, worker completion deadline in hours
        """
        if not build_conf:
            raise OsbsValidationException('build_conf must be defined')

        build_from = build_from or build_conf.get_build_from()
        self.scratch.value = build_conf.get_scratch(scratch)
        orchestrator_deadline = build_conf.get_orchestor_deadline()
        worker_deadline = build_conf.get_worker_deadline()

        self.component.value = component
        self.koji_target.value = koji_target
        self.koji_task_id.value = koji_task_id
        self.platform.value = platform
        self.reactor_config_map.value = build_conf.get_reactor_config_map()
        self.reactor_config_override.value = reactor_config_override
        self.signing_intent.value = signing_intent
        self.user.value = user

        if not build_from:
            raise OsbsValidationException('build_from must be defined')

        if ':' not in build_from:
            raise OsbsValidationException('build_from must be "source_type:source_value"')
        source_type, source_value = build_from.split(':', 1)
        if source_type not in ('image', 'imagestream'):
            raise OsbsValidationException(
                'first part in build_from, may be only image or imagestream')
        if source_type == 'imagestream':
            self.buildroot_is_imagestream.value = True
        self.build_from.value = build_from
        self.build_image.value = source_value

        try:
            self.orchestrator_deadline.value = int(orchestrator_deadline)
        except (ValueError, TypeError):
            self.orchestrator_deadline.value = ORCHESTRATOR_MAX_RUNTIME
        try:
            self.worker_deadline.value = int(worker_deadline)
        except (ValueError, TypeError):
            self.worker_deadline.value = WORKER_MAX_RUNTIME

        self._populate_image_tag()

    def _populate_image_tag(self):
        timestamp = utcnow().strftime('%Y%m%d%H%M%S')
        # RNG is seeded once its imported, so in cli calls scratch builds would get unique name.
        # On brew builders we import osbs once - thus RNG is seeded once and `randrange`
        # returns the same values throughout the life of the builder.
        # Before each `randrange` call we should be calling `.seed` to prevent this
        random.seed()

        tag_segments = [
            self.koji_target.value or 'none',
            str(random.randrange(10**(RAND_DIGITS - 1), 10**RAND_DIGITS)),
            timestamp
        ]

        if self.platform.value and (self.arrangement_version.value or 0) >= 4:
            tag_segments.append(self.platform.value)

        tag = '-'.join(tag_segments)
        self.image_tag.value = '{}/{}:{}'.format(self.user.value, self.component.value, tag)

    def validate(self):
        logger.info("Validating params of %s", self.__class__.__name__)
        for param in self.required_params:
            if param.value is None:
                if param.allow_none:
                    logger.debug("param '%s' is None; None is allowed", param.name)
                else:
                    logger.error("param '%s' is None; None is NOT allowed", param.name)
                    raise OsbsValidationException("param '%s' is not valid: None is not allowed" %
                                                  param.name)

    def __repr__(self):
        return "{}({})".format(self.__class__.__name__, self.__dict__)

    def from_json(self, user_params_json):
        if not user_params_json:
            return
        try:
            json_dict = json.loads(user_params_json)
        except ValueError:
            logger.debug('failed to convert %s', user_params_json)
            raise
        for key, value in json_dict.items():
            try:
                self.convert_dict[key].value = value
            except KeyError:
                continue

    def set_if_exists(self, json_dict, param):
        if self.convert_dict[param].value:
            json_dict[param] = self.convert_dict[param].value

    def to_dict(self, keys):
        retdict = {}
        for key in keys:
            self.set_if_exists(retdict, key)
        return retdict

    def to_json(self):
        json_dict = self.to_dict(list(self.convert_dict))
        return json.dumps(json_dict, sort_keys=True)


@register_user_params
class BuildUserParams(BuildCommon):

    KIND = USER_PARAMS_KIND_IMAGE_BUILDS

    def __init__(self, build_json_store=None, customize_conf=None):
        # defines image_tag, koji_target, filesystem_koji_task_id, platform, arrangement_version
        super(BuildUserParams, self).__init__(build_json_store=build_json_store)
        self.additional_tags = BuildParam('additional_tags', allow_none=True)
        self.base_image = BuildParam('base_image', allow_none=True)
        self.build_type = BuildParam('build_type')
        self.compose_ids = BuildParam("compose_ids", allow_none=True)
        self.customize_conf_path = BuildParam("customize_conf", allow_none=True,
                                              default=customize_conf or DEFAULT_CUSTOMIZE_CONF)
        self.dependency_replacements = BuildParam("dependency_replacements")
        self.filesystem_koji_task_id = BuildParam("filesystem_koji_task_id", allow_none=True)
        self.flatpak = BuildParam('flatpak', default=False)
        self.git_branch = BuildParam('git_branch')
        self.git_commit_depth = BuildParam('git_commit_depth', allow_none=True)
        self.git_ref = BuildParam('git_ref', default=DEFAULT_GIT_REF)
        self.git_uri = BuildParam('git_uri')
        self.imagestream_name = BuildParam('imagestream_name')
        self.is_auto = BuildParam('is_auto', allow_none=True)
        self.isolated = BuildParam('isolated', allow_none=True)
        self.koji_parent_build = BuildParam('koji_parent_build', allow_none=True)
        self.koji_upload_dir = BuildParam('koji_upload_dir', allow_none=True)
        self.name = BuildIDParam()
        self.operator_bundle_replacement_pullspecs = BuildParam(
            'operator_bundle_replacement_pullspecs', allow_none=True
        )
        self.operator_manifests_extract_platform = BuildParam('operator_manifests_extract_platform',
                                                              allow_none=True)
        self.parent_images_digests = BuildParam('parent_images_digests', allow_none=True)
        self.platforms = BuildParam('platforms', allow_none=True)
        self.release = BuildParam('release', allow_none=True)
        self.remote_source_build_args = BuildParam('remote_source_build_args', allow_none=True)
        self.remote_source_url = BuildParam('remote_source_url', allow_none=True)
        self.skip_build = BuildParam('skip_build', allow_none=True)
        self.tags_from_yaml = BuildParam('tags_from_yaml', allow_none=True)
        self.trigger_imagestreamtag = BuildParam('trigger_imagestreamtag')
        self.triggered_after_koji_task = BuildParam('triggered_after_koji_task', allow_none=True)
        self.yum_repourls = BuildParam("yum_repourls")

        self.auto_build_node_selector = None
        self.explicit_build_node_selector = None
        self.isolated_build_node_selector = None
        self.platform_node_selector = None
        self.scratch_build_node_selector = None

        self.required_params.extend([
            self.build_type,
            self.git_ref,
            self.git_uri,
        ])
        self.repo_info = None

        self.attrs_finalizer()

    def set_params(self,
                   additional_tags=None,
                   base_image=None,
                   build_conf=None,
                   build_type=None,
                   compose_ids=None,
                   dependency_replacements=None,
                   filesystem_koji_task_id=None,
                   flatpak=None,
                   git_branch=None,
                   git_commit_depth=None,
                   git_ref=None,
                   git_uri=None,
                   is_auto=None,
                   isolated=None,
                   koji_parent_build=None,
                   koji_upload_dir=None,
                   name_label=None,
                   operator_bundle_replacement_pullspecs=None,
                   operator_manifests_extract_platform=None,
                   auto_build_node_selector=None,
                   explicit_build_node_selector=None,
                   isolated_build_node_selector=None,
                   platform_node_selector=None,
                   scratch_build_node_selector=None,
                   parent_images_digests=None,
                   platform=None,
                   platforms=None,
                   release=None,
                   remote_source_url=None,
                   remote_source_build_args=None,
                   repo_info=None,
                   skip_build=None,
                   tags_from_yaml=None,
                   triggered_after_koji_task=None,
                   yum_repourls=None,
                   **kwargs):
        """
        set parameters in the user parameters. Others are set in the super functions

        these parameters are accepted:
        :param build_conf: BuildConfiguration, optional build configuration
        :param build_type: str, orchestrator or worker
        :param compose_ids: list of int, ODCS composes to use instead of generating new ones
        :param dependency_replacements: list of str, dependencies to be replaced by cachito, as
        pkg_manager:name:version[:new_name]
        :param filesystem_koji_task_id: int, Koji Task that created the base filesystem
        :param flatpak: if we should build a Flatpak OCI Image
        :param git_branch: str, branch name of the branch to be pulled
        :param git_ref: str, commit ID of the branch to be pulled
        :param git_uri: str, uri of the git repository for the source
        :param is_auto: bool, build as a automatic build
        :param isolated: bool, build as an isolated build
        :param koji_parent_build: str,
        :param koji_upload_dir: str, koji directory where the completed image will be uploaded
        :param name_label: str, label of the parent image
        :param user: str, name of the user requesting the build
        :param operator_bundle_replacement_pullspecs: dict, mapping of original pullspecs to
                                                      replacement pullspecs for operator manifest
                                                      bundle builds
        :param operator_manifests_extract_platform: str, indicates which platform should upload
                                                    operator manifests to koji
        :param parent_images_digests: dict, mapping image digests to names and platforms
        :param platforms: list of str, platforms to build on
        :param platform: str, platform
        :param reactor_config_map: str, name of the config map containing the reactor environment
        :param reactor_config_override: dict, data structure for reactor config to be injected as
        an environment variable into a worker build;
        when used, reactor_config_map is ignored.
        :param release: str,

        :param repo_info: RepoInfo, git repo data for the build
        :param scratch: bool, build as a scratch build
        :param signing_intent: bool, True to sign the resulting image
        :param skip_build: bool, if we should skip build and just set buildconfig for autorebuilds
        :param triggered_after_koji_task: int, koji task ID from which was autorebuild triggered
        :param yum_repourls: list of str, uris of the yum repos to pull from

        Please keep the paramater list alphabetized for easier tracking of changes

        the following parameters are pulled from the BuildConfiguration (ie, build_conf)
        :param auto_build_node_selector: dict, a nodeselector for auto builds
        :param explicit_build_node_selector: dict, a nodeselector for explicit builds
        :param isolated_build_node_selector: dict, a nodeselector for isolated builds
        :param platform_node_selector: dict, a nodeselector for a user_paramsific platform
        :param scratch_build_node_selector: dict, a nodeselector for scratch builds

        the following parameters can be pulled from the RepoInfo (ie, repo_info)
        :param git_branch: str, branch name of the branch to be pulled
        :param git_ref: str, commit ID of the branch to be pulled
        :param git_uri: str, uri of the git repository for the source
        """
        super(BuildUserParams, self).set_params(build_conf=build_conf, platform=platform,
                                                **kwargs)
        if repo_info:
            additional_tags = repo_info.additional_tags.tags
            git_branch = repo_info.git_branch
            git_commit_depth = repo_info.git_commit_depth
            git_ref = repo_info.git_ref
            git_uri = repo_info.git_uri
            tags_from_yaml = repo_info.additional_tags.from_container_yaml
            self.repo_info = repo_info
        elif not git_uri:
            raise OsbsValidationException('no repo_info passed to BuildUserParams')

        auto_build_node_selector = build_conf.get_auto_build_node_selector()
        explicit_build_node_selector = build_conf.get_explicit_build_node_selector()
        isolated_build_node_selector = build_conf.get_isolated_build_node_selector()
        platform_node_selector = build_conf.get_platform_node_selector(platform)
        scratch_build_node_selector = build_conf.get_scratch_build_node_selector()

        self.additional_tags.value = additional_tags or set()
        self.git_branch.value = git_branch
        self.git_commit_depth.value = git_commit_depth
        self.git_ref.value = git_ref
        self.git_uri.value = git_uri

        self.remote_source_url.value = remote_source_url
        self.remote_source_build_args.value = remote_source_build_args
        self.release.value = release
        self.build_type.value = build_type

        self.name.value = make_name_from_git(self.git_uri.value, self.git_branch.value)

        self.filesystem_koji_task_id.value = filesystem_koji_task_id
        self.is_auto.value = is_auto
        self.isolated.value = isolated
        self.flatpak.value = flatpak
        self.koji_parent_build.value = koji_parent_build
        self.koji_upload_dir.value = koji_upload_dir
        self.parent_images_digests.value = parent_images_digests
        self.platforms.value = platforms
        self.operator_manifests_extract_platform.value = operator_manifests_extract_platform
        self.operator_bundle_replacement_pullspecs.value = operator_bundle_replacement_pullspecs
        self.skip_build.value = skip_build
        self.tags_from_yaml.value = tags_from_yaml
        self.triggered_after_koji_task.value = triggered_after_koji_task

        if not base_image:
            # For flatpaks, we can set this later from the reactor config
            if not flatpak:
                raise OsbsValidationException("base_image must be provided")
        else:
            self.set_base_image(base_image)

        if not name_label:
            raise OsbsValidationException("name_label must be provided")
        self.imagestream_name.value = name_label.replace('/', '-')

        if kwargs.get('signing_intent') and compose_ids:
            raise OsbsValidationException(
                'Please only define signing_intent -OR- compose_ids, not both')
        if not (compose_ids is None or isinstance(compose_ids, list)):
            raise OsbsValidationException("compose_ids must be a list")
        if not (dependency_replacements is None or isinstance(dependency_replacements, list)):
            raise OsbsValidationException("dependency_replacements must be a list")
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.compose_ids.value = compose_ids or []
        self.dependency_replacements.value = dependency_replacements or []
        self.yum_repourls.value = yum_repourls or []

        if (self.scratch.value, self.is_auto.value, self.isolated.value).count(True) > 1:
            raise OsbsValidationException(
                'Build variations are mutually exclusive. '
                'Must set either scratch, is_auto, isolated, or none. ')
        self.auto_build_node_selector = auto_build_node_selector or {}
        self.explicit_build_node_selector = explicit_build_node_selector or {}
        self.isolated_build_node_selector = isolated_build_node_selector or {}
        self.platform_node_selector = platform_node_selector or {}
        self.scratch_build_node_selector = scratch_build_node_selector or {}

    def set_base_image(self, base_image):
        self.base_image.value = base_image
        self.trigger_imagestreamtag.value = get_imagestreamtag_from_image(base_image)


@register_user_params
class SourceContainerUserParams(BuildCommon):
    """User params for building source containers"""

    KIND = USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS

    def __init__(self, build_json_store=None):
        super(SourceContainerUserParams, self).__init__(build_json_store=build_json_store)
        self.sources_for_koji_build_nvr = BuildParam("sources_for_koji_build_nvr", allow_none=True)
        self.sources_for_koji_build_id = BuildParam("sources_for_koji_build_id", allow_none=True)

        self.attrs_finalizer()

    def set_params(
        self,
        sources_for_koji_build_nvr=None,
        sources_for_koji_build_id=None,
        **kwargs
    ):
        """
        :param str sources_for_koji_build_nvr: NVR of build that will be used
                                               to fetch sources
        :param int sources_for_koji_build_id: ID of build that will be used
                                              to fetch sources
        :return:
        """
        super(SourceContainerUserParams, self).set_params(**kwargs)

        if sources_for_koji_build_id is None and sources_for_koji_build_nvr is None:
            raise OsbsValidationException(
                "At least one param from 'sources_for_koji_build_id' or "
                "'sources_for_koji_build_nvr' must be specified"
            )
        self.sources_for_koji_build_nvr.value = sources_for_koji_build_nvr
        self.sources_for_koji_build_id.value = sources_for_koji_build_id
