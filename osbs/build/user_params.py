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
from osbs.constants import (DEFAULT_GIT_REF, DEFAULT_CUSTOMIZE_CONF, RAND_DIGITS,
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
    kind = json_dict.pop(KIND_KEY, BuildUserParams.KIND)  # BW comp. default to BuildUserParams
    user_params_class = user_param_kinds[kind]
    return user_params_class.from_json(user_params_json)


class BuildCommon(BuildParamsBase):
    """Common user parameters, class should be considered abstract"""

    # Must be defined in subclasses
    KIND = NotImplemented

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
    orchestrator_deadline = BuildParam("orchestrator_deadline")
    pipeline_run_name = BuildParam("pipeline_run_name")
    platform = BuildParam("platform")
    reactor_config_map = BuildParam("reactor_config_map")
    reactor_config_override = BuildParam("reactor_config_override")
    scratch = BuildParam("scratch")
    signing_intent = BuildParam("signing_intent")
    user = BuildParam("user", required=True)
    worker_deadline = BuildParam("worker_deadline")

    def __setattr__(self, name, value):
        super(BuildCommon, self).__setattr__(name, value)
        logger.debug("%s = %s", name, value)

    @classmethod
    def make_params(cls,
                    build_conf=None,
                    build_from=None,
                    build_json_dir=None,
                    component=None,
                    koji_target=None,
                    koji_task_id=None,
                    pipeline_run_name=None,
                    platform=None,
                    reactor_config_override=None,
                    scratch=None,
                    signing_intent=None,
                    user=None,
                    **kwargs):
        """
        Create a user_params instance.

        Most parameters will simply be used as the value of the corresponding BuildParam.
        The notable exception is `build_conf`, which contains values for other params but
        is not a BuildParam itself (list of params set from build_conf can be found below).

        Arguments that are None (either passed as None, or None by default) are ignored.
        This is important to avoid overwriting default values of params. Once the instance
        is created, however, overwriting defaults by setting None is allowed, e.g.:

        >>> params = BuildCommon.make_params(build_conf=bc)  # does not overwrite defaults
        >>> params.version = None  # does overwrite the default

        these parameters are accepted:
        :param base_image: str, name of the parent image
        :param build_conf: BuildConfiguration, the build configuration
        :param build_from: str, buildroot reference (image or imagestream)
        :param build_json_dir: str, path to directory with JSON build templates
        :param component: str, name of the component
        :param koji_parent_build: str,
        :param koji_target: str, koji tag with packages used to build the image
        :param koji_task_id: str, koji ID
        :param koji_upload_dir: str, koji directory where the completed image will be uploaded
        :param pipeline_run_name: str, name of the pipeline run
        :param platform: str, platform
        :param reactor_config_override: dict, data structure for reactor config to be injected as
                                        an environment variable into a worker build;
                                        when used, reactor_config_map is ignored.
        :param scratch: bool, build as a scratch build (if not specified in build_conf)
        :param signing_intent: bool, True to sign the resulting image
        :param user: str, name of the user requesting the build

        Please keep the paramater list alphabetized for easier tracking of changes

        the following parameters are pulled from the BuildConfiguration (ie, build_conf)
        :param build_from: str, buildroot reference (if not specified as argument)
        :param orchestrator_deadline: int, orchestrator deadline in hours
        :param reactor_config_map: str, name of the config map containing the reactor environment
        :param scratch: bool, build as a scratch build
        :param worker_deadline: int, worker completion deadline in hours
        """
        if not build_conf:
            raise OsbsValidationException('build_conf must be defined')

        build_from = build_from or build_conf.get_build_from()
        if not build_from:
            raise OsbsValidationException('build_from must be defined')
        if ':' not in build_from:
            raise OsbsValidationException('build_from must be "source_type:source_value"')
        source_type, source_value = build_from.split(':', 1)
        if source_type not in ('image', 'imagestream'):
            raise OsbsValidationException(
                'first part in build_from, may be only image or imagestream')

        try:
            orchestrator_deadline = int(build_conf.get_orchestor_deadline())
        except (ValueError, TypeError):
            orchestrator_deadline = ORCHESTRATOR_MAX_RUNTIME
        try:
            worker_deadline = int(build_conf.get_worker_deadline())
        except (ValueError, TypeError):
            worker_deadline = WORKER_MAX_RUNTIME

        if build_conf.get_scratch(scratch):
            reactor_config = build_conf.get_reactor_config_map_scratch()
        else:
            reactor_config = build_conf.get_reactor_config_map()
        # Update kwargs with arguments explicitly accepted by this method
        kwargs.update({
            "build_json_dir": build_json_dir,
            "component": component,
            "koji_target": koji_target,
            "koji_task_id": koji_task_id,
            "platform": platform,
            "reactor_config_override": reactor_config_override,
            "signing_intent": signing_intent,
            "user": user,
            # Potentially pulled from build_conf
            "build_from": build_from,
            "build_image": source_value,
            "buildroot_is_imagestream": (source_type == "imagestream"),
            "orchestrator_deadline": orchestrator_deadline,
            "pipeline_run_name": pipeline_run_name,
            "reactor_config_map": reactor_config,
            "scratch": build_conf.get_scratch(scratch),
            "worker_deadline": worker_deadline,
        })

        # Drop arguments that are:
        # - unknown; some callers may pass deprecated params
        # - not set (set to None, either explicitly or implicitly)
        kwargs = {
            k: v for k, v in kwargs.items()
            if v is not None and cls.get_param(k) is not None
        }

        params = cls(**kwargs)
        params._populate_image_tag()
        return params

    @classmethod
    def _make_params_super(cls, *args, **kwargs):
        # Pylint cannot properly infer the return type of an overridden classmethod
        # that returns cls(). This is an ugly workaround that prevents pylint from
        # inferring any type at all (thus preventing false-positive warnings).
        # See https://github.com/PyCQA/pylint/issues/981
        return BuildCommon.make_params.__func__(cls, *args, **kwargs)

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

        if self.platform:
            tag_segments.append(self.platform)

        tag = '-'.join(tag_segments)
        self.image_tag = '{}/{}:{}'.format(self.user, self.component, tag)

    def validate(self):
        logger.info("Validating params of %s", self.__class__.__name__)
        # pylint: disable=not-an-iterable; pylint does not understand metaclass properties
        missing = [p for p in self.__class__.required_params if p.__get__(self) is None]
        if missing:
            missing_repr = ", ".join(repr(p.name) for p in missing)
            raise OsbsValidationException("Missing required params: {}".format(missing_repr))

    @classmethod
    def from_json(cls, user_params_json):
        if not user_params_json:
            return cls()
        try:
            json_dict = json.loads(user_params_json)
        except ValueError:
            logger.debug('failed to convert %s', user_params_json)
            raise
        # Drop invalid keys
        json_dict = {k: v for k, v in json_dict.items() if cls.get_param(k) is not None}
        return cls(**json_dict)

    def to_dict(self, keys):
        retdict = {}
        for key in keys:
            value = getattr(self, key)
            if value:
                retdict[key] = value
        return retdict

    def to_json(self):
        # pylint: disable=not-an-iterable; pylint does not understand metaclass properties
        keys = (p.name for p in self.__class__.params if p.include_in_json)
        json_dict = self.to_dict(keys)
        json_dict[KIND_KEY] = self.KIND
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
    isolated = BuildParam("isolated")
    koji_parent_build = BuildParam("koji_parent_build")
    koji_upload_dir = BuildParam("koji_upload_dir")
    name = BuildIDParam()
    operator_bundle_replacement_pullspecs = BuildParam("operator_bundle_replacement_pullspecs")
    operator_csv_modifications_url = BuildParam("operator_csv_modifications_url")
    operator_manifests_extract_platform = BuildParam("operator_manifests_extract_platform")
    parent_images_digests = BuildParam("parent_images_digests")
    platforms = BuildParam("platforms")
    release = BuildParam("release")
    remote_sources = BuildParam("remote_sources")
    tags_from_yaml = BuildParam("tags_from_yaml")
    trigger_imagestreamtag = BuildParam("trigger_imagestreamtag")
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

    @classmethod
    def make_params(cls,
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
                    isolated=None,
                    koji_parent_build=None,
                    koji_upload_dir=None,
                    name_label=None,
                    operator_bundle_replacement_pullspecs=None,
                    operator_csv_modifications_url=None,
                    operator_manifests_extract_platform=None,
                    parent_images_digests=None,
                    platform=None,
                    platforms=None,
                    release=None,
                    remote_sources=None,
                    repo_info=None,
                    tags_from_yaml=None,
                    yum_repourls=None,
                    **kwargs):
        """
        Create a BuildUserParams instance.

        Like the parent method, most params are simply used as values for the corresponding
        BuildParam, this time with two notable exceptions: `build_conf` and `repo_info`.
        Compared to the parent method, this one pulls even more param values from `build_conf`
        and may also pull some values from `repo_info` (see below).

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
        :param isolated: bool, build as an isolated build
        :param koji_parent_build: str,
        :param koji_upload_dir: str, koji directory where the completed image will be uploaded
        :param name_label: str, label of the parent image
        :param user: str, name of the user requesting the build
        :param operator_bundle_replacement_pullspecs: dict, mapping of original pullspecs to
                                                      replacement pullspecs for operator manifest
                                                      bundle builds
        :param operator_csv_modifications_url: str, URL to JSON file describing operator CSV changes
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
        :param remote_sources: list of dicts, each dict contains info about particular
        remote source with the following keys:
            build_args: dict, extra args for `builder.build_args`, if any
            configs: list of str, configuration files to be injected into
            the exploded remote sources dir
            request_id: int, cachito request id; used to request the
            Image Content Manifest
            url: str, URL from which to download a source archive
            name: str, name of remote source
        :param repo_info: RepoInfo, git repo data for the build
        :param scratch: bool, build as a scratch build
        :param signing_intent: bool, True to sign the resulting image
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
        if repo_info:
            additional_tags = repo_info.additional_tags.tags
            git_branch = repo_info.git_branch
            git_commit_depth = repo_info.git_commit_depth
            git_ref = repo_info.git_ref
            git_uri = repo_info.git_uri
            tags_from_yaml = repo_info.additional_tags.from_container_yaml
        elif not git_uri:
            raise OsbsValidationException('no repo_info passed to BuildUserParams')

        # For flatpaks, we can set this later from the reactor config
        if not base_image and not flatpak:
            raise OsbsValidationException("base_image must be provided")

        if not name_label:
            raise OsbsValidationException("name_label must be provided")

        if kwargs.get('signing_intent') and compose_ids:
            raise OsbsValidationException(
                'Please only define signing_intent -OR- compose_ids, not both')
        if not (compose_ids is None or isinstance(compose_ids, list)):
            raise OsbsValidationException("compose_ids must be a list")
        if not (dependency_replacements is None or isinstance(dependency_replacements, list)):
            raise OsbsValidationException("dependency_replacements must be a list")
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")

        kwargs.update({
            "base_image": base_image,
            "build_conf": build_conf,
            "build_type": build_type,
            "compose_ids": compose_ids or [],
            "dependency_replacements": dependency_replacements or [],
            "filesystem_koji_task_id": filesystem_koji_task_id,
            "flatpak": flatpak,
            "imagestream_name": name_label,
            "include_koji_repo": include_koji_repo,
            "isolated": isolated,
            "koji_parent_build": koji_parent_build,
            "koji_upload_dir": koji_upload_dir,
            "operator_bundle_replacement_pullspecs": operator_bundle_replacement_pullspecs,
            "operator_csv_modifications_url": operator_csv_modifications_url,
            "operator_manifests_extract_platform": operator_manifests_extract_platform,
            "parent_images_digests": parent_images_digests,
            "platform": platform,
            "platforms": platforms,
            "release": release,
            "remote_sources": remote_sources,
            "trigger_imagestreamtag": base_image,
            "yum_repourls": yum_repourls or [],
            # Potentially pulled from repo_info
            "additional_tags": additional_tags or set(),
            "git_branch": git_branch,
            "git_commit_depth": git_commit_depth,
            "git_ref": git_ref,
            "git_uri": git_uri,
            "name": make_name_from_git(git_uri, git_branch),
            "tags_from_yaml": tags_from_yaml,
            # Pulled from build_conf
            "auto_build_node_selector": build_conf.get_auto_build_node_selector() or {},
            "explicit_build_node_selector": build_conf.get_explicit_build_node_selector() or {},
            "isolated_build_node_selector": build_conf.get_isolated_build_node_selector() or {},
            "platform_node_selector": build_conf.get_platform_node_selector(platform) or {},
            "scratch_build_node_selector": build_conf.get_scratch_build_node_selector() or {},
        })

        params = cls._make_params_super(**kwargs)

        if (params.scratch, params.isolated).count(True) > 1:
            raise OsbsValidationException(
                'Build variations are mutually exclusive. '
                'Must set either scratch, isolated, or none. ')

        return params

    def set_base_image(self, base_image):
        self.base_image = base_image
        self.trigger_imagestreamtag = base_image


@register_user_params
class SourceContainerUserParams(BuildCommon):
    """User params for building source containers"""

    KIND = USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS

    sources_for_koji_build_nvr = BuildParam("sources_for_koji_build_nvr")
    sources_for_koji_build_id = BuildParam("sources_for_koji_build_id")

    @classmethod
    def make_params(
        cls,
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
        if sources_for_koji_build_id is None and sources_for_koji_build_nvr is None:
            raise OsbsValidationException(
                "At least one param from 'sources_for_koji_build_id' or "
                "'sources_for_koji_build_nvr' must be specified"
            )
        kwargs.update({
            "sources_for_koji_build_id": sources_for_koji_build_id,
            "sources_for_koji_build_nvr": sources_for_koji_build_nvr,
        })
        return cls._make_params_super(**kwargs)
