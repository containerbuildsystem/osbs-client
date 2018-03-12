"""
Copyright (c) 2018 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import json

from osbs.build.spec import BuildParam, BuildIDParam, UserParam, BuildCommon
from osbs.constants import DEFAULT_GIT_REF, REACTOR_CONFIG_ARRANGEMENT_VERSION
from osbs.exceptions import OsbsValidationException
from osbs.utils import get_imagestreamtag_from_image, make_name_from_git
from osbs.repo_utils import AdditionalTagsConfig


logger = logging.getLogger(__name__)


class BuildUserParams(BuildCommon):
    def __init__(self, build_json_dir=None):
        # defines image_tag, koji_target, filesystem_koji_task_id, platform, arrangement_version
        super(BuildUserParams, self).__init__()
        self.build_json_dir = BuildParam('build_json_dir', default=build_json_dir)
        self.git_uri = BuildParam('git_uri')
        self.git_ref = BuildParam('git_ref', default=DEFAULT_GIT_REF)
        self.git_branch = BuildParam('git_branch')
        self.user = UserParam()
        self.component = BuildParam('component')
        self.name = BuildIDParam()
        self.build_image = BuildParam('build_image')
        self.build_imagestream = BuildParam('build_imagestream')
        self.build_from = BuildParam('build_from')
        self.build_type = BuildParam('build_type')
        self.trigger_imagestreamtag = BuildParam('trigger_imagestreamtag')
        self.imagestream_name = BuildParam('imagestream_name')
        self.platforms = BuildParam("platforms", allow_none=True)
        self.release = BuildParam("release", allow_none=True)
        self.isolated = BuildParam("release", allow_none=True)
        self.scratch = BuildParam("release", allow_none=True)
        self.koji_task_id = BuildParam("koji_task_id", allow_none=True)
        self.koji_parent_build = BuildParam("koji_parent_build", allow_none=True)
        self.flatpak = BuildParam("flatpak", default=False)
        self.flatpak_base_image = BuildParam("flatpak_base_image", allow_none=True)
        self.signing_intent = BuildParam("signing_intent", allow_none=True)
        self.yum_repourls = BuildParam("yum_repourls")
        self.compose_ids = BuildParam("compose_ids", allow_none=True)
        self.reactor_config_map = BuildParam("reactor_config_map", allow_none=True)
        self.additional_tags = None
        self.arrangement_version.value = REACTOR_CONFIG_ARRANGEMENT_VERSION
        self.required_params = [
            self.build_json_dir,
            self.build_type,
            self.git_ref,
            self.git_uri,
            self.koji_target,
            self.user,
        ]
        self.convert_dict = {
            'arrangement_version': self.arrangement_version,  # calculated value
            'build_from': self.build_from,
            'build_json_dir': self.build_json_dir,  # init paramater
            'build_image': self.build_image,
            'build_imagestream': self.build_imagestream,
            'build_type': self.build_type,
            'component': self.component,
            'compose_ids': self.compose_ids,
            'filesystem_koji_task_id': self.filesystem_koji_task_id,
            'flatpak': self.flatpak,
            'flatpak_base_image': self.flatpak_base_image,
            'git_branch': self.git_branch,
            'git_ref': self.git_ref,
            'git_uri': self.git_uri,
            'image_tag': self.image_tag,
            'imagestream_name': self.imagestream_name,
            'isolated': self.isolated,
            'koji_parent_build': self.koji_parent_build,
            'koji_target': self.koji_target,
            'name': self.name,  # calculated value
            'platform': self.platform,
            'platforms': self.platforms,
            'reactor_config_map': self.reactor_config_map,
            'release': self.release,
            'scratch': self.scratch,
            'signing_intent': self.signing_intent,
            'task_id': self.koji_task_id,
            'trigger_imagestreamtag': self.trigger_imagestreamtag,
            'user': self.user,
            'yum_repourls': self.yum_repourls,
        }

    def set_params(self,
                   git_uri=None, git_ref=None, git_branch=None,
                   base_image=None, name_label=None,
                   user=None, additional_tag_data=None,
                   component=None, release=None,
                   build_image=None, build_imagestream=None, build_from=None,
                   platforms=None, platform=None, build_type=None,
                   koji_target=None, koji_task_id=None, filesystem_koji_task_id=None,
                   koji_parent_build=None,
                   flatpak=None, flatpak_base_image=None,
                   reactor_config_map=None,
                   yum_repourls=None, signing_intent=None, compose_ids=None,
                   isolated=None, scratch=None,
                   **kwargs):
        self.git_uri.value = git_uri
        self.git_ref.value = git_ref
        self.git_branch.value = git_branch
        self.user.value = user
        self.component.value = component
        self.release.value = release
        self.build_type.value = build_type

        self.name.value = make_name_from_git(self.git_uri.value, self.git_branch.value)
        self.reactor_config_map.value = reactor_config_map

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
            source_type, source_value = build_from.split(':', 1)
            if source_type not in ('image', 'imagestream'):
                raise OsbsValidationException(
                    'first part in build_from, may be only image or imagestream')
            if source_type == 'image':
                self.build_image.value = source_value
            else:
                self.build_imagestream.value = source_value

        self.platforms.value = platforms
        self.platform.value = platform
        self.koji_target.value = koji_target
        self.koji_task_id.value = koji_task_id
        self.filesystem_koji_task_id.value = filesystem_koji_task_id
        self.koji_parent_build.value = koji_parent_build
        self.flatpak.value = flatpak
        self.flatpak_base_image.value = flatpak_base_image
        self.isolated.value = isolated
        self.scratch.value = scratch

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
        if compose_ids and yum_repourls:
            raise OsbsValidationException(
                'Please only define yum_repourls -OR- compose_ids, not both')
        if not (compose_ids is None or isinstance(compose_ids, list)):
            raise OsbsValidationException("compose_ids must be a list")
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.yum_repourls.value = yum_repourls or []
        self.signing_intent.value = signing_intent
        self.compose_ids.value = compose_ids or []

        if additional_tag_data:
            self.additional_tags = AdditionalTagsConfig(additional_tag_data['dir_path'],
                                                        additional_tag_data['file_name'],
                                                        additional_tag_data['tags'])
        self._populate_image_tag()

    def __repr__(self):
        return "UserParams(%s)" % self.__dict__

    def from_json(self, user_params_json):
        if not user_params_json:
            return
        json_dict = json.loads(user_params_json)
        logger.debug("CALLED FROM JSON HERE with dict %s", json_dict)
        for key, value in json_dict.items():
            try:
                self.convert_dict[key].value = value
            except KeyError:
                continue
        if 'additional_tags' in json_dict.keys():
            logger.debug("read additional tags %s from json", json_dict['additional_tags'])
            self.additional_tags = AdditionalTagsConfig(tags=json_dict['additional_tags'])

    def set_if_exists(self, json_dict, param):
        if self.convert_dict[param].value:
            json_dict[param] = self.convert_dict[param].value

    def to_json(self):
        json_dict = {}
        for key in self.convert_dict:
            self.set_if_exists(json_dict, key)
        if self.additional_tags:
            json_dict['additional_tags'] = sorted(self.additional_tags.tags)
        return json.dumps(json_dict, sort_keys=True)
