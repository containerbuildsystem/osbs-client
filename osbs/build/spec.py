"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Specifications of build types.
"""
from __future__ import print_function, absolute_import, unicode_literals

from collections import namedtuple
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


class RegistryURIsParam(BuildParam):
    """
    Build parameter for a list of registry URIs

    Each registry has a URI (hostname) and a version (str).
    """

    name = "registry_uris"
    RegistryURI = namedtuple("RegistryURI", ['uri', 'version'])

    def __init__(self):
        super(RegistryURIsParam, self).__init__(self.name)

    @BuildParam.value.setter
    def value(self, val):  # pylint: disable=W0221
        registry_uris = []
        for uri in val:
            if '/' in uri:
                (hostname, version) = uri.split('/', 1)
            else:
                hostname = uri
                version = 'v1'

            registry_uris.append(self.RegistryURI(hostname, version))

        BuildParam.value.fset(self, registry_uris)


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
    user = UserParam()
    component = BuildParam('component')
    registry_uris = RegistryURIsParam()
    source_registry_uri = BuildParam('source_registry_uri')
    openshift_uri = BuildParam('openshift_uri')
    builder_openshift_url = BuildParam('builder_openshift_url')
    name = BuildIDParam()
    yum_repourls = BuildParam("yum_repourls")
    use_auth = BuildParam("use_auth", allow_none=True)

    def __init__(self):
        self.required_params = [
            self.git_uri,
            self.git_ref,
            self.user,
            self.component,
            self.registry_uris,
            self.openshift_uri,
        ]

    def set_params(self, git_uri=None, git_ref=None,
                   registry_uri=None,  # compatibility name for registry_uris
                   registry_uris=None, user=None,
                   component=None, openshift_uri=None, source_registry_uri=None,
                   yum_repourls=None, use_auth=None, builder_openshift_url=None):
        self.git_uri.value = git_uri
        self.git_ref.value = git_ref
        self.user.value = user
        self.component.value = component

        def ditch_http_prefix(val):
            if not val:
                return val
            # We don't want the scheme
            return re.sub(r'^https?://(.*)$',
                          lambda m: m.groups()[0],
                          val)

        # registry_uri is the compatibility name for registry_uris
        if registry_uri is not None:
            assert registry_uris is None
            registry_uris = [registry_uri]

        self.registry_uris.value = [ditch_http_prefix(uri)
                                    for uri in registry_uris or []]
        self.source_registry_uri.value = ditch_http_prefix(source_registry_uri)
        self.openshift_uri.value = openshift_uri
        self.builder_openshift_url.value = builder_openshift_url
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.yum_repourls.value = yum_repourls or []
        self.use_auth.value = use_auth


class ProdSpec(CommonSpec):
    git_branch = BuildParam('git_branch')
    trigger_imagestreamtag = BuildParam('trigger_imagestreamtag')
    imagestream_name = BuildParam('imagestream_name')
    imagestream_url = BuildParam('imagestream_url')
    sources_command = BuildParam("sources_command")
    architecture = BuildParam("architecture")
    vendor = BuildParam("vendor")
    build_host = BuildParam("build_host")
    authoritative_registry = BuildParam("authoritative_registry ")
    distribution_scope = BuildParam("distribution_scope")
    registry_api_versions = BuildParam("registry_api_versions")
    koji_target = BuildParam("koji_target", allow_none=True)
    kojiroot = BuildParam("kojiroot", allow_none=True)
    kojihub = BuildParam("kojihub", allow_none=True)
    image_tag = BuildParam("image_tag")
    pulp_secret = BuildParam("pulp_secret", allow_none=True)
    pulp_sync_secret = BuildParam("pulp_sync_secret", allow_none=True)
    pulp_registry = BuildParam("pulp_registry", allow_none=True)
    pulp_sync_registry = BuildParam("pulp_sync_registry", allow_none=True)
    pdc_secret = BuildParam("pdc_secret", allow_none=True)
    pdc_url = BuildParam("pdc_url", allow_none=True)
    smtp_uri = BuildParam("smtp_uri", allow_none=True)
    nfs_server_path = BuildParam("nfs_server_path", allow_none=True)
    nfs_dest_dir = BuildParam("nfs_dest_dir", allow_none=True)
    git_push_url = BuildParam("git_push_url", allow_none=True)
    git_push_username = BuildParam("git_push_username", allow_none=True)
    builder_build_json_dir = BuildParam("builder_build_json_dir", allow_none=True)

    def __init__(self):
        super(ProdSpec, self).__init__()
        self.required_params += [
            self.sources_command,
            self.vendor,
            self.build_host,
            self.authoritative_registry,
            self.distribution_scope,
            self.registry_api_versions,
            self.koji_target,
            self.kojiroot,
            self.kojihub,
            self.pulp_secret,
            self.pulp_sync_secret,
            self.pulp_registry,
            self.pulp_sync_registry,
            self.pdc_secret,
            self.pdc_url,
            self.smtp_uri,
            self.nfs_server_path,
            self.git_push_url,
            self.git_push_username,
        ]

    def set_params(self, sources_command=None, architecture=None, vendor=None,
                   build_host=None, authoritative_registry=None, distribution_scope=None,
                   koji_target=None, kojiroot=None, kojihub=None,
                   source_secret=None,  # compatibility name for pulp_secret
                   pulp_secret=None, pulp_sync_secret=None,
                   pulp_registry=None, pulp_sync_registry=None,
                   pdc_secret=None, pdc_url=None,
                   smtp_uri=None, nfs_server_path=None,
                   nfs_dest_dir=None, git_branch=None, base_image=None,
                   name_label=None, git_push_url=None, git_push_username=None,
                   builder_build_json_dir=None,
                   registry_api_versions=None, **kwargs):
        super(ProdSpec, self).set_params(**kwargs)
        self.sources_command.value = sources_command
        self.architecture.value = architecture
        self.vendor.value = vendor
        self.build_host.value = build_host
        self.authoritative_registry.value = authoritative_registry
        self.distribution_scope.value = distribution_scope
        self.registry_api_versions.value = registry_api_versions
        self.koji_target.value = koji_target
        self.kojiroot.value = kojiroot
        self.kojihub.value = kojihub
        self.pulp_secret.value = pulp_secret or source_secret
        self.pulp_sync_secret.value = pulp_sync_secret
        self.pulp_registry.value = pulp_registry
        self.pulp_sync_registry.value = pulp_sync_registry
        self.pdc_secret.value = pdc_secret
        self.pdc_url.value = pdc_url
        self.smtp_uri.value = smtp_uri
        self.nfs_server_path.value = nfs_server_path
        self.nfs_dest_dir.value = nfs_dest_dir
        self.git_push_url.value = git_push_url
        self.git_push_username.value = git_push_username
        self.git_branch.value = git_branch
        repo = git_repo_humanish_part_from_uri(self.git_uri.value)
        self.name.value = "{repo}-{branch}".format(repo=repo,
                                                   branch=git_branch)
        self.trigger_imagestreamtag.value = get_imagestreamtag_from_image(base_image)
        self.builder_build_json_dir.value = builder_build_json_dir
        self.imagestream_name.value = name_label.replace('/', '-')
        primary_registry_uri = self.registry_uris.value[0].uri
        self.imagestream_url.value = os.path.join(primary_registry_uri,
                                                  name_label)
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.image_tag.value = "%s/%s:%s-%s" % (
            self.user.value,
            self.component.value,
            self.koji_target.value or 'none',
            timestamp
        )


class SimpleSpec(CommonSpec):
    image_tag = BuildParam("image_tag")

    def set_params(self, tag=None, **kwargs):
        super(SimpleSpec, self).set_params(**kwargs)
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.name.value = "build-%s" % timestamp

        self.image_tag.value = "%s/%s:%s" % (
            self.user.value,
            self.component.value,
            tag or timestamp
        )
