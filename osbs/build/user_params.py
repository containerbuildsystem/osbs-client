"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import re
import random
import json

from osbs.build.user_params_meta import BuildParam, BuildParamsBase
from osbs.constants import (DEFAULT_GIT_REF, REACTOR_CONFIG_ARRANGEMENT_VERSION,
                            DEFAULT_CUSTOMIZE_CONF, RAND_DIGITS,
                            WORKER_MAX_RUNTIME, ORCHESTRATOR_MAX_RUNTIME,
                            USER_PARAMS_KIND_IMAGE_BUILDS,
                            USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS,
                            )
from osbs.exceptions import OsbsValidationException
from osbs.utils import (make_name_from_git, utcnow)


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


class UserParam(BuildParam):
    """ custom class for "user" parameter with postprocessing """

    def __init__(self, **kwargs):
        super(UserParam, self).__init__("user", **kwargs)


class BuildIDParam(BuildParam):
    """ validate build ID """

    def __init__(self, **kwargs):
        super(BuildIDParam, self).__init__("name", **kwargs)

    def __set__(self, obj, value):
        # build ID has to conform to:
        #  * 63 chars at most
        #  * (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?

        if len(value) > 63:
            # component + timestamp > 63
            new_name = value[:63]
            logger.warning("'%s' is too long, changing to '%s'", value, new_name)
            value = new_name

        build_id_re = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
        match = build_id_re.match(value)
        if not match:
            logger.error("'%s' is not valid build ID", value)
            raise OsbsValidationException("Build ID '%s', doesn't match regex '%s'" %
                                          (value, build_id_re))
        super(BuildIDParam, self).__set__(obj, value)


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


class BuildCommon(BuildParamsBase):
    """Common user parameters, class should be considered abstract"""

    # Must be defined in subclasses
    KIND = NotImplemented

    arrangement_version = BuildParam("arrangement_version",
                                     default=REACTOR_CONFIG_ARRANGEMENT_VERSION)
    # build_from contains the full build_from string, including the source type prefix
    build_from = BuildParam("build_from")
    # build_image contains the buildroot name, whether the buildroot is a straight image or an
    # imagestream.  buildroot_is_imagestream indicates what type of buildroot
    build_image = BuildParam("build_image")
    buildroot_is_imagestream = BuildParam("buildroot_is_imagestream", default=False)
    build_json_dir = BuildParam("build_json_dir", required=True)
    component = BuildParam("component")
    image_tag = BuildParam("image_tag")
    koji_target = BuildParam("koji_target")
    koji_task_id = BuildParam("koji_task_id")
    platform = BuildParam("platform")
    orchestrator_deadline = BuildParam("orchestrator_deadline")
    reactor_config_map = BuildParam("reactor_config_map")
    reactor_config_override = BuildParam("reactor_config_override")
    scratch = BuildParam("scratch")
    signing_intent = BuildParam("signing_intent")
    user = UserParam(required=True)
    worker_deadline = BuildParam("worker_deadline")

    def __init__(self, build_json_dir=None, **kwargs):
        super(BuildCommon, self).__init__(build_json_dir=build_json_dir, **kwargs)

    def __setattr__(self, name, value):
        super(BuildCommon, self).__setattr__(name, value)
        logger.debug("%s = %s", name, value)

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
        self.scratch = build_conf.get_scratch(scratch)
        orchestrator_deadline = build_conf.get_orchestor_deadline()
        worker_deadline = build_conf.get_worker_deadline()

        self.component = component
        self.koji_target = koji_target
        self.koji_task_id = koji_task_id
        self.platform = platform
        self.reactor_config_map = build_conf.get_reactor_config_map()
        self.reactor_config_override = reactor_config_override
        self.signing_intent = signing_intent
        self.user = user

        if not build_from:
            raise OsbsValidationException('build_from must be defined')

        if ':' not in build_from:
            raise OsbsValidationException('build_from must be "source_type:source_value"')
        source_type, source_value = build_from.split(':', 1)
        if source_type not in ('image', 'imagestream'):
            raise OsbsValidationException(
                'first part in build_from, may be only image or imagestream')
        if source_type == 'imagestream':
            self.buildroot_is_imagestream = True
        self.build_from = build_from
        self.build_image = source_value

        try:
            self.orchestrator_deadline = int(orchestrator_deadline)
        except (ValueError, TypeError):
            self.orchestrator_deadline = ORCHESTRATOR_MAX_RUNTIME
        try:
            self.worker_deadline = int(worker_deadline)
        except (ValueError, TypeError):
            self.worker_deadline = WORKER_MAX_RUNTIME

        self._populate_image_tag()

    def _populate_image_tag(self):
        timestamp = utcnow().strftime('%Y%m%d%H%M%S')
        # RNG is seeded once its imported, so in cli calls scratch builds would get unique name.
        # On brew builders we import osbs once - thus RNG is seeded once and `randrange`
        # returns the same values throughout the life of the builder.
        # Before each `randrange` call we should be calling `.seed` to prevent this
        random.seed()

        tag_segments = [
            self.koji_target or 'none',
            str(random.randrange(10**(RAND_DIGITS - 1), 10**RAND_DIGITS)),
            timestamp
        ]

        if self.platform and (self.arrangement_version or 0) >= 4:
            tag_segments.append(self.platform)

        tag = '-'.join(tag_segments)
        self.image_tag = '{}/{}:{}'.format(self.user, self.component, tag)

    def validate(self):
        logger.info("Validating params of %s", self.__class__.__name__)
        missing = [p for p in self.__class__.required_params if p.__get__(self) is None]
        if missing:
            missing_repr = ", ".join(repr(p.name) for p in missing)
            raise OsbsValidationException("Missing required params: {}".format(missing_repr))

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
                setattr(self, key, value)
            except AttributeError:
                continue

    def to_dict(self, keys):
        retdict = {}
        for key in keys:
            value = getattr(self, key)
            if value:
                retdict[key] = value
        return retdict

    def to_json(self):
        keys = (p.name for p in self.__class__.params if p.include_in_json)
        json_dict = self.to_dict(keys)
        return json.dumps(json_dict, sort_keys=True)


@register_user_params
class BuildUserParams(BuildCommon):

    KIND = USER_PARAMS_KIND_IMAGE_BUILDS

    additional_tags = BuildParam("additional_tags")
    base_image = BuildParam("base_image")
    build_type = BuildParam("build_type", required=True)
    compose_ids = BuildParam("compose_ids")
    customize_conf = BuildParam("customize_conf", default=DEFAULT_CUSTOMIZE_CONF)
    dependency_replacements = BuildParam("dependency_replacements")
    filesystem_koji_task_id = BuildParam("filesystem_koji_task_id")
    flatpak = BuildParam("flatpak", default=False)
    git_branch = BuildParam("git_branch")
    git_commit_depth = BuildParam("git_commit_depth")
    git_ref = BuildParam("git_ref", default=DEFAULT_GIT_REF, required=True)
    git_uri = BuildParam("git_uri", required=True)
    imagestream_name = BuildParam("imagestream_name")
    include_koji_repo = BuildParam("include_koji_repo", default=False)
    is_auto = BuildParam("is_auto")
    isolated = BuildParam("isolated")
    kind = BuildParam("kind", default=KIND)
    koji_parent_build = BuildParam("koji_parent_build")
    koji_upload_dir = BuildParam("koji_upload_dir")
    name = BuildIDParam()
    operator_bundle_replacement_pullspecs = BuildParam("operator_bundle_replacement_pullspecs")
    operator_manifests_extract_platform = BuildParam("operator_manifests_extract_platform")
    parent_images_digests = BuildParam("parent_images_digests")
    platforms = BuildParam("platforms")
    release = BuildParam("release")
    remote_source_build_args = BuildParam("remote_source_build_args")
    remote_source_configs = BuildParam("remote_source_configs")
    remote_source_url = BuildParam("remote_source_url")
    skip_build = BuildParam("skip_build")
    tags_from_yaml = BuildParam("tags_from_yaml")
    trigger_imagestreamtag = BuildParam("trigger_imagestreamtag")
    triggered_after_koji_task = BuildParam("triggered_after_koji_task")
    yum_repourls = BuildParam("yum_repourls")

    auto_build_node_selector = BuildParam("auto_build_node_selector",
                                          include_in_json=False)
    explicit_build_node_selector = BuildParam("explicit_build_node_selector",
                                              include_in_json=False)
    isolated_build_node_selector = BuildParam("isolated_build_node_selector",
                                              include_in_json=False)
    platform_node_selector = BuildParam("platform_node_selector",
                                        include_in_json=False)
    scratch_build_node_selector = BuildParam("scratch_build_node_selector",
                                             include_in_json=False)

    repo_info = BuildParam("repo_info", include_in_json=False)

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
                   include_koji_repo=None,
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
                   remote_source_build_args=None,
                   remote_source_configs=None,
                   remote_source_url=None,
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
        :param include_koji_repo: include the repo from the target build tag, even if other
                                                   repourls are provided.
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

        self.additional_tags = additional_tags or set()
        self.git_branch = git_branch
        self.git_commit_depth = git_commit_depth
        self.git_ref = git_ref
        self.git_uri = git_uri

        self.remote_source_build_args = remote_source_build_args
        self.remote_source_configs = remote_source_configs
        self.remote_source_url = remote_source_url
        self.release = release
        self.build_type = build_type

        self.name = make_name_from_git(self.git_uri, self.git_branch)

        self.filesystem_koji_task_id = filesystem_koji_task_id
        self.is_auto = is_auto
        self.isolated = isolated
        self.flatpak = flatpak
        self.include_koji_repo = include_koji_repo
        self.koji_parent_build = koji_parent_build
        self.koji_upload_dir = koji_upload_dir
        self.parent_images_digests = parent_images_digests
        self.platforms = platforms
        self.operator_manifests_extract_platform = operator_manifests_extract_platform
        self.operator_bundle_replacement_pullspecs = operator_bundle_replacement_pullspecs
        self.skip_build = skip_build
        self.tags_from_yaml = tags_from_yaml
        self.triggered_after_koji_task = triggered_after_koji_task

        if not base_image:
            # For flatpaks, we can set this later from the reactor config
            if not flatpak:
                raise OsbsValidationException("base_image must be provided")
        else:
            self.set_base_image(base_image)

        if not name_label:
            raise OsbsValidationException("name_label must be provided")
        self.imagestream_name = name_label

        if kwargs.get('signing_intent') and compose_ids:
            raise OsbsValidationException(
                'Please only define signing_intent -OR- compose_ids, not both')
        if not (compose_ids is None or isinstance(compose_ids, list)):
            raise OsbsValidationException("compose_ids must be a list")
        if not (dependency_replacements is None or isinstance(dependency_replacements, list)):
            raise OsbsValidationException("dependency_replacements must be a list")
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.compose_ids = compose_ids or []
        self.dependency_replacements = dependency_replacements or []
        self.yum_repourls = yum_repourls or []

        if (self.scratch, self.is_auto, self.isolated).count(True) > 1:
            raise OsbsValidationException(
                'Build variations are mutually exclusive. '
                'Must set either scratch, is_auto, isolated, or none. ')
        self.auto_build_node_selector = auto_build_node_selector or {}
        self.explicit_build_node_selector = explicit_build_node_selector or {}
        self.isolated_build_node_selector = isolated_build_node_selector or {}
        self.platform_node_selector = platform_node_selector or {}
        self.scratch_build_node_selector = scratch_build_node_selector or {}

    def set_base_image(self, base_image):
        self.base_image = base_image
        self.trigger_imagestreamtag = base_image


@register_user_params
class SourceContainerUserParams(BuildCommon):
    """User params for building source containers"""

    KIND = USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS

    kind = BuildParam("kind", default=KIND)
    sources_for_koji_build_nvr = BuildParam("sources_for_koji_build_nvr")
    sources_for_koji_build_id = BuildParam("sources_for_koji_build_id")

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
        self.sources_for_koji_build_nvr = sources_for_koji_build_nvr
        self.sources_for_koji_build_id = sources_for_koji_build_id
