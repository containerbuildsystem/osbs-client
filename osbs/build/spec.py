"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Specifications of build types.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import datetime
import os
import re
from osbs.constants import DEFAULT_GIT_REF
from osbs.exceptions import OsbsValidationException
from osbs.utils import (get_imagestreamtag_from_image,
                        git_repo_humanish_part_from_uri)


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

    @BuildParam.value.setter
    def value(self, val):  # pylint: disable=W0221
        try:
            val = val.ljust(4, "_")  # py3
        except TypeError:
            val = val.ljust(4, b"_")  # py2
        BuildParam.value.fset(self, val)


class BuildIDParam(BuildParam):
    """ validate build ID """
    name = "name"

    def __init__(self):
        super(BuildIDParam, self).__init__(self.name)

    @BuildParam.value.setter
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
        BuildParam.value.fset(self, val)


class BuildTypeSpec(object):
    """ Abstract baseclass for specification of a buildtype """
    required_params = None

    def validate(self):
        logger.info("Validating params of %s", self.__class__.__name__)
        for param in self.required_params:
            if param.value is None:
                if param.allow_none:
                    logger.debug("param '%s' is None; None is allowed", param.name)
                else:
                    logger.error("param '%s' is None; None is NOT allowed", param.name)
                    raise OsbsValidationException("param '%s' is not valid: None is not allowed" % param.name)

    def __repr__(self):
        return "Spec(%s)" % self.__dict__


class CommonSpec(BuildTypeSpec):
    git_uri = BuildParam('git_uri')
    git_ref = BuildParam('git_ref', default=DEFAULT_GIT_REF)
    git_branch = BuildParam('git_branch')
    user = UserParam()
    component = BuildParam('component')
    trigger_imagestreamtag = BuildParam('trigger_imagestreamtag')
    imagestream_name = BuildParam('imagestream_name')
    imagestream_url = BuildParam('imagestream_url')
    registry_uri = BuildParam('registry_uri')
    openshift_uri = BuildParam('openshift_uri')
    name = BuildIDParam()
    yum_repourls = BuildParam("yum_repourls")
    use_auth = BuildParam("use_auth", allow_none=True)

    def __init__(self):
        self.required_params = [
            self.git_uri,
            self.git_ref,
            self.user,
            self.component,
            self.registry_uri,
            self.openshift_uri,
        ]

    def set_params(self, git_uri=None, git_ref=None, git_branch=None, registry_uri=None, user=None,
                   component=None, base_image=None, name_label=None, openshift_uri=None,
                   yum_repourls=None, use_auth=None, **kwargs):
        self.git_uri.value = git_uri
        self.git_ref.value = git_ref
        self.git_branch.value = git_branch
        self.user.value = user
        self.component.value = component
        # We only want the hostname[:port]
        self.registry_uri.value = re.sub(r'^https?://([^/]*)/?.*',
                                         lambda m: m.groups()[0],
                                         registry_uri)
        self.openshift_uri.value = openshift_uri
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.yum_repourls.value = yum_repourls or []
        self.use_auth.value = use_auth
        repo = git_repo_humanish_part_from_uri(git_uri)
        self.name.value = "{repo}-{branch}".format(repo=repo,
                                                   branch=git_branch)
        self.trigger_imagestreamtag.value = get_imagestreamtag_from_image(base_image)
        self.imagestream_name.value = name_label.replace('/', '-')
        self.imagestream_url.value = os.path.join(self.registry_uri.value, name_label)


class ProdSpec(CommonSpec):
    sources_command = BuildParam("sources_command")
    architecture = BuildParam("architecture")
    vendor = BuildParam("vendor")
    build_host = BuildParam("build_host")
    authoritative_registry = BuildParam("authoritative_registry ")
    koji_target = BuildParam("koji_target", allow_none=True)
    kojiroot = BuildParam("kojiroot", allow_none=True)
    kojihub = BuildParam("kojihub", allow_none=True)
    image_tag = BuildParam("image_tag")
    source_secret = BuildParam("source_secret", allow_none=True)
    pulp_registry = BuildParam("pulp_registry", allow_none=True)
    nfs_server_path = BuildParam("nfs_server_path", allow_none=True)
    nfs_dest_dir = BuildParam("nfs_dest_dir", allow_none=True)
    git_push_url = BuildParam("git_push_url", allow_none=True)
    git_push_username = BuildParam("git_push_username", allow_none=True)

    def __init__(self):
        super(ProdSpec, self).__init__()
        self.required_params += [
            self.sources_command,
            self.architecture,
            self.vendor,
            self.build_host,
            self.authoritative_registry,
            self.koji_target,
            self.kojiroot,
            self.kojihub,
            self.source_secret,
            self.pulp_registry,
            self.nfs_server_path,
            self.git_push_url,
            self.git_push_username,
        ]

    def set_params(self, sources_command=None, architecture=None, vendor=None,
                   build_host=None, authoritative_registry=None,
                   koji_target=None, kojiroot=None, kojihub=None,
                   source_secret=None, pulp_registry=None, nfs_server_path=None,
                   nfs_dest_dir=None, git_push_url=None, git_push_username=None,
                   **kwargs):
        super(ProdSpec, self).set_params(**kwargs)
        self.sources_command.value = sources_command
        self.architecture.value = architecture
        self.vendor.value = vendor
        self.build_host.value = build_host
        self.authoritative_registry.value = authoritative_registry
        self.koji_target.value = koji_target
        self.kojiroot.value = kojiroot
        self.kojihub.value = kojihub
        self.source_secret.value = source_secret
        self.pulp_registry.value = pulp_registry
        self.nfs_server_path.value = nfs_server_path
        self.nfs_dest_dir.value = nfs_dest_dir
        self.git_push_url.value = git_push_url
        self.git_push_username.value = git_push_username
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.image_tag.value = "%s/%s:%s-%s" % (
            self.user.value,
            self.component.value,
            self.koji_target.value,
            timestamp
        )


class SimpleSpec(CommonSpec):
    image_tag = BuildParam("image_tag")

    def set_params(self, **kwargs):
        super(SimpleSpec, self).set_params(**kwargs)
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.image_tag.value = "%s/%s:%s" % (self.user.value, self.component.value, timestamp)
