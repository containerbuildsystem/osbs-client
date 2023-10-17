"""
Copyright (c) 2018-2022 Red Hat, Inc
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
from osbs.constants import (DEFAULT_GIT_REF, RAND_DIGITS,
                            USER_PARAMS_KIND_IMAGE_BUILDS,
                            USER_PARAMS_KIND_SOURCE_CONTAINER_BUILDS)
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

    component = BuildParam("component")
    image_tag = BuildParam("image_tag")
    koji_target = BuildParam("koji_target")
    koji_task_id = BuildParam("koji_task_id")
    platform = BuildParam("platform")
    reactor_config_map = BuildParam("reactor_config_map")
    scratch = BuildParam("scratch")
    signing_intent = BuildParam("signing_intent")
    user = BuildParam("user", required=True)
    userdata = BuildParam("userdata")

    def __setattr__(self, name, value):
        super(BuildCommon, self).__setattr__(name, value)
        logger.debug("%s = %s", name, value)

    @classmethod
    def make_params(cls,
                    build_conf=None,
                    component=None,
                    koji_target=None,
                    koji_task_id=None,
                    platform=None,
                    scratch=None,
                    signing_intent=None,
                    user=None,
                    userdata=None,
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
        :param component: str, name of the component
        :param koji_parent_build: str,
        :param koji_target: str, koji tag with packages used to build the image
        :param koji_task_id: int, koji *task* ID
        :param platform: str, platform
        :param scratch: bool, build as a scratch build (if not specified in build_conf)
        :param signing_intent: bool, True to sign the resulting image
        :param user: str, name of the user requesting the build
        :param userdata: dict, custom user data

        Please keep the paramater list alphabetized for easier tracking of changes

        the following parameters are pulled from the BuildConfiguration (ie, build_conf)
        :param reactor_config_map: str, name of the config map containing the reactor environment
        :param scratch: bool, build as a scratch build
        """
        if not build_conf:
            raise OsbsValidationException('build_conf must be defined')

        if build_conf.get_scratch(scratch):
            reactor_config = build_conf.get_reactor_config_map_scratch()
        else:
            reactor_config = build_conf.get_reactor_config_map()
        # Update kwargs with arguments explicitly accepted by this method
        kwargs.update({
            "component": component,
            "koji_target": koji_target,
            "koji_task_id": koji_task_id,
            "platform": platform,
            "signing_intent": signing_intent,
            "user": user,
            "userdata": userdata,
            # Potentially pulled from build_conf
            "reactor_config_map": reactor_config,
            "scratch": build_conf.get_scratch(scratch),
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
    compose_ids = BuildParam("compose_ids")
    dependency_replacements = BuildParam("dependency_replacements")
    flatpak = BuildParam("flatpak", default=False)
    git_branch = BuildParam("git_branch")
    git_commit_depth = BuildParam("git_commit_depth")
    git_ref = BuildParam("git_ref", default=DEFAULT_GIT_REF, required=True)
    git_uri = BuildParam("git_uri", required=True)
    include_koji_repo = BuildParam("include_koji_repo", default=False)
    isolated = BuildParam("isolated")
    koji_parent_build = BuildParam("koji_parent_build")
    name = BuildIDParam()
    operator_csv_modifications_url = BuildParam("operator_csv_modifications_url")
    platforms = BuildParam("platforms")
    release = BuildParam("release")
    remote_sources = BuildParam("remote_sources")
    tags_from_yaml = BuildParam("tags_from_yaml")
    yum_repourls = BuildParam("yum_repourls")

    @classmethod
    def make_params(cls,
                    additional_tags=None,
                    base_image=None,
                    build_conf=None,
                    compose_ids=None,
                    dependency_replacements=None,
                    flatpak=None,
                    git_branch=None,
                    git_commit_depth=None,
                    git_ref=None,
                    git_uri=None,
                    include_koji_repo=None,
                    isolated=None,
                    koji_parent_build=None,
                    name_label=None,
                    operator_csv_modifications_url=None,
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
        :param compose_ids: list of int, ODCS composes to use instead of generating new ones
        :param dependency_replacements: list of str, dependencies to be replaced by cachito, as
        pkg_manager:name:version[:new_name]
        :param flatpak: if we should build a Flatpak OCI Image
        :param git_branch: str, branch name of the branch to be pulled
        :param git_ref: str, commit ID of the branch to be pulled
        :param git_uri: str, uri of the git repository for the source
        :param include_koji_repo: include the repo from the target build tag, even if other
                                                   repourls are provided.
        :param isolated: bool, build as an isolated build
        :param koji_parent_build: str,
        :param name_label: str, label of the parent image
        :param user: str, name of the user requesting the build
        :param operator_csv_modifications_url: str, URL to JSON file describing operator CSV changes
        :param platforms: list of str, platforms to build on
        :param platform: str, platform
        :param reactor_config_map: str, name of the config map containing the reactor environment
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
            "compose_ids": compose_ids or [],
            "dependency_replacements": dependency_replacements or [],
            "flatpak": flatpak,
            "include_koji_repo": include_koji_repo,
            "isolated": isolated,
            "koji_parent_build": koji_parent_build,
            "operator_csv_modifications_url": operator_csv_modifications_url,
            "platform": platform,
            "platforms": platforms,
            "release": release,
            "remote_sources": remote_sources,
            "yum_repourls": yum_repourls or [],
            # Potentially pulled from repo_info
            "additional_tags": additional_tags or set(),
            "git_branch": git_branch,
            "git_commit_depth": git_commit_depth,
            "git_ref": git_ref,
            "git_uri": git_uri,
            "name": make_name_from_git(git_uri, git_branch),
            "tags_from_yaml": tags_from_yaml,
        })

        params = cls._make_params_super(**kwargs)

        if (params.scratch, params.isolated).count(True) > 1:
            raise OsbsValidationException(
                'Build variations are mutually exclusive. '
                'Must set either scratch, isolated, or none. ')

        return params

    def set_base_image(self, base_image):
        self.base_image = base_image


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
