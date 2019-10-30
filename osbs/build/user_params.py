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

from osbs.constants import (DEFAULT_GIT_REF, REACTOR_CONFIG_ARRANGEMENT_VERSION,
                            DEFAULT_CUSTOMIZE_CONF, RAND_DIGITS,
                            WORKER_MAX_RUNTIME, ORCHESTRATOR_MAX_RUNTIME)
from osbs.exceptions import OsbsValidationException
from osbs.utils import get_imagestreamtag_from_image, make_name_from_git, RegistryURI, utcnow


logger = logging.getLogger(__name__)


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


class RegistryURIsParam(BuildParam):
    """
    Build parameter for a list of registry URIs

    Each registry has a full URI, a docker URI, and a version (str).
    """

    name = "registry_uris"

    def __init__(self):
        super(RegistryURIsParam, self).__init__(self.name)

    @BuildParam.value.setter  # pylint: disable=no-member
    def value(self, val):  # pylint: disable=W0221
        registry_uris = [RegistryURI(uri) for uri in val]
        BuildParam.value.fset(self, registry_uris)  # pylint: disable=no-member


class BuildCommon(object):
    def __init__(self, build_json_dir=None):
        self.arrangement_version = BuildParam("arrangement_version", allow_none=True)
        self.build_json_dir = BuildParam('build_json_dir', default=build_json_dir)
        self.component = BuildParam('component')
        self.filesystem_koji_task_id = BuildParam("filesystem_koji_task_id", allow_none=True)
        self.image_tag = BuildParam("image_tag")
        self.koji_target = BuildParam("koji_target", allow_none=True)
        self.koji_task_id = BuildParam('koji_task_id', allow_none=True)
        self.platform = BuildParam("platform", allow_none=True)
        self.orchestrator_deadline = BuildParam('orchestrator_deadline', allow_none=True)
        self.scratch = BuildParam('scratch', allow_none=True)
        self.user = UserParam()
        self.worker_deadline = BuildParam('worker_deadline', allow_none=True)

        self.required_params = [
            self.build_json_dir,
            self.koji_target,
            self.user,
        ]
        self.convert_dict = {}

        # Defaults
        self.arrangement_version.value = REACTOR_CONFIG_ARRANGEMENT_VERSION

    def attrs_finalizer(self):
        for _, param in self.__dict__.items():
            if isinstance(param, BuildParam):
                # check that every parameter has a unique name
                if param.name in self.convert_dict:
                    raise OsbsValidationException('Two user params with the same name')
                self.convert_dict[param.name] = param

    def set_params(
        self,
        component=None,
        koji_target=None,
        koji_task_id=None,
        orchestrator_deadline=None,
        platform=None,
        scratch=None,
        user=None,
        worker_deadline=None,
        **kwargs
    ):
        self.component.value = component
        self.koji_target.value = koji_target
        self.koji_task_id.value = koji_task_id
        self.platform.value = platform
        self.scratch.value = scratch
        self.user.value = user

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


class BuildUserParams(BuildCommon):
    def __init__(self, build_json_dir=None, customize_conf=None):
        # defines image_tag, koji_target, filesystem_koji_task_id, platform, arrangement_version
        super(BuildUserParams, self).__init__(build_json_dir=build_json_dir)
        self.base_image = BuildParam('base_image', allow_none=True)
        self.build_from = BuildParam('build_from')
        self.build_image = BuildParam('build_image')
        self.build_imagestream = BuildParam('build_imagestream')
        self.build_type = BuildParam('build_type')
        self.compose_ids = BuildParam("compose_ids", allow_none=True)
        self.customize_conf_path = BuildParam("customize_conf", allow_none=True,
                                              default=customize_conf or DEFAULT_CUSTOMIZE_CONF)
        self.flatpak = BuildParam('flatpak', default=False)
        self.git_branch = BuildParam('git_branch')
        self.git_ref = BuildParam('git_ref', default=DEFAULT_GIT_REF)
        self.git_uri = BuildParam('git_uri')
        self.imagestream_name = BuildParam('imagestream_name')
        self.isolated = BuildParam('isolated', allow_none=True)
        self.koji_parent_build = BuildParam('koji_parent_build', allow_none=True)
        self.koji_upload_dir = BuildParam('koji_upload_dir', allow_none=True)
        self.name = BuildIDParam()
        self.parent_images_digests = BuildParam('parent_images_digests', allow_none=True)
        self.operator_manifests_extract_platform = BuildParam('operator_manifests_extract_platform',
                                                              allow_none=True)
        self.platforms = BuildParam('platforms', allow_none=True)
        self.reactor_config_map = BuildParam("reactor_config_map", allow_none=True)
        self.reactor_config_override = BuildParam("reactor_config_override", allow_none=True)
        self.release = BuildParam('release', allow_none=True)
        self.signing_intent = BuildParam('signing_intent', allow_none=True)
        self.trigger_imagestreamtag = BuildParam('trigger_imagestreamtag')
        self.yum_repourls = BuildParam("yum_repourls")
        self.tags_from_yaml = BuildParam('tags_from_yaml', allow_none=True)
        self.additional_tags = BuildParam('additional_tags', allow_none=True)
        self.git_commit_depth = BuildParam('git_commit_depth', allow_none=True)
        self.triggered_after_koji_task = BuildParam('triggered_after_koji_task', allow_none=True)

        self.required_params.extend([
            self.build_type,
            self.git_ref,
            self.git_uri,
        ])

        self.attrs_finalizer()

    def set_params(self,
                   git_uri=None, git_ref=None, git_branch=None,
                   base_image=None, name_label=None,
                   release=None,
                   build_image=None, build_imagestream=None, build_from=None,
                   platforms=None, build_type=None,
                   filesystem_koji_task_id=None,
                   koji_parent_build=None, koji_upload_dir=None,
                   flatpak=None, reactor_config_map=None, reactor_config_override=None,
                   yum_repourls=None, signing_intent=None, compose_ids=None,
                   isolated=None, parent_images_digests=None,
                   tags_from_yaml=None, additional_tags=None,
                   git_commit_depth=None,
                   operator_manifests_extract_platform=None,
                   triggered_after_koji_task=None, **kwargs):
        super(BuildUserParams, self).set_params(**kwargs)
        self.git_uri.value = git_uri
        self.git_ref.value = git_ref
        self.git_branch.value = git_branch
        self.git_commit_depth.value = git_commit_depth
        self.tags_from_yaml.value = tags_from_yaml
        self.additional_tags.value = additional_tags or set()

        self.release.value = release
        self.build_type.value = build_type
        self.base_image.value = base_image

        self.name.value = make_name_from_git(self.git_uri.value, self.git_branch.value)
        self.reactor_config_map.value = reactor_config_map
        self.reactor_config_override.value = reactor_config_override

        unique_build_args = (build_imagestream, build_image, build_from)
        if sum(bool(a) for a in unique_build_args) != 1:
            raise OsbsValidationException(
                'Please only define one of build_from, build_image, build_imagestream')
        self.build_image.value = build_image
        self.build_imagestream.value = build_imagestream
        if self.build_image.value or self.build_imagestream.value:
            logger.warning("build_image or build_imagestream is defined, they are deprecated,"
                           "use build_from instead")

        if build_from:
            if ':' not in build_from:
                raise OsbsValidationException(
                        'build_from must be "source_type:source_value"')
            source_type, source_value = build_from.split(':', 1)
            if source_type not in ('image', 'imagestream'):
                raise OsbsValidationException(
                    'first part in build_from, may be only image or imagestream')
            if source_type == 'image':
                self.build_image.value = source_value
            else:
                self.build_imagestream.value = source_value

        self.parent_images_digests.value = parent_images_digests
        self.operator_manifests_extract_platform.value = operator_manifests_extract_platform
        self.platforms.value = platforms
        self.filesystem_koji_task_id.value = filesystem_koji_task_id
        self.koji_parent_build.value = koji_parent_build
        self.koji_upload_dir.value = koji_upload_dir
        self.flatpak.value = flatpak
        self.isolated.value = isolated
        self.triggered_after_koji_task.value = triggered_after_koji_task

        if not flatpak:
            if not base_image:
                raise OsbsValidationException("base_image must be provided")
            self.trigger_imagestreamtag.value = get_imagestreamtag_from_image(base_image)

            if not name_label:
                raise OsbsValidationException("name_label must be provided")
            self.imagestream_name.value = name_label.replace('/', '-')

        if signing_intent and compose_ids:
            raise OsbsValidationException(
                'Please only define signing_intent -OR- compose_ids, not both')
        if not (compose_ids is None or isinstance(compose_ids, list)):
            raise OsbsValidationException("compose_ids must be a list")
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.yum_repourls.value = yum_repourls or []
        self.signing_intent.value = signing_intent
        self.compose_ids.value = compose_ids or []


class SourceContainerUserParams(BuildCommon):
    """User params for building source containers"""

    def __init__(self, build_json_dir=None):
        super(SourceContainerUserParams, self).__init__(
            build_json_dir=build_json_dir)
        self.sources_for_koji_build_nvr = BuildParam("sources_for_koji_build_nvr")

        self.required_params.extend([
            self.sources_for_koji_build_nvr,
        ])

        self.attrs_finalizer()

    def set_params(
        self,
        sources_for_koji_build_nvr=None,
        **kwargs
    ):
        """
        :param str sources_for_koji_build_nvr: NVR of build that will be used
                                               to fetch sources
        :return:
        """
        super(SourceContainerUserParams, self).set_params(**kwargs)
        self.sources_for_koji_build_nvr.value = sources_for_koji_build_nvr
