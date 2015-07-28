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
import re
from osbs.constants import DEFAULT_GIT_REF
from osbs.exceptions import OsbsValidationException


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
        d = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

        # build ID has to conform to:
        #  * 63 chars at most
        #  * (([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?
        build_id = "%s-%s" % (val, d)

        if len(build_id) > 63:
            # component + timestamp > 63
            d_len = len(d)
            new_prefix = val[:63 - d_len - 1]
            new_name = "%s-%s" % (new_prefix, d)
            logger.warning("'%s' is too long, changing to '%s'", build_id, new_name)
            build_id = new_name

        build_id_re = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
        match = build_id_re.match(build_id)
        if not match:
            logger.error("'%s' is not valid build ID", build_id)
            raise OsbsValidationException("Build ID '%s', doesn't match regex '%s'" %
                                          (build_id, build_id_re))
        BuildParam.value.fset(self, build_id)


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
    registry_uri = BuildParam('registry_uri')
    openshift_uri = BuildParam('openshift_uri')
    name = BuildIDParam()
    yum_repourls = BuildParam("yum_repourls")
    metadata_plugin_use_auth = BuildParam("metadata_plugin_use_auth", allow_none=True)  # for debugging

    def __init__(self):
        self.required_params = [
            self.git_uri,
            self.git_ref,
            self.user,
            self.component,
            self.registry_uri,
            self.openshift_uri,
        ]

    def set_params(self, git_uri=None, git_ref=None, registry_uri=None, user=None,
                   component=None, openshift_uri=None, yum_repourls=None,
                   metadata_plugin_use_auth=None, **kwargs):
        self.git_uri.value = git_uri
        self.git_ref.value = git_ref
        self.user.value = user
        self.component.value = component
        self.registry_uri.value = registry_uri
        self.openshift_uri.value = openshift_uri
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.yum_repourls.value = yum_repourls or []
        self.metadata_plugin_use_auth.value = metadata_plugin_use_auth
        self.name.value = self.component.value


class CommonProdSpec(CommonSpec):
    sources_command = BuildParam("sources_command")
    architecture = BuildParam("architecture")
    vendor = BuildParam("vendor")
    build_host = BuildParam("build_host")
    authoritative_registry = BuildParam("authoritative_registry ")
    scratch_build = BuildParam("scratch_build")

    def __init__(self):
        super(CommonProdSpec, self).__init__()
        self.required_params += [
            self.sources_command,
            self.architecture,
            self.vendor,
            self.build_host,
            self.authoritative_registry,
        ]

    def set_params(self, sources_command=None, architecture=None, vendor=None,
                   build_host=None, authoritative_registry=None,
                   scratch_build=None, **kwargs):
        super(CommonProdSpec, self).set_params(**kwargs)
        self.sources_command.value = sources_command
        self.architecture.value = architecture
        self.vendor.value = vendor
        self.build_host.value = build_host
        self.authoritative_registry.value = authoritative_registry
        self.scratch_build.value = scratch_build


class ProdSpec(CommonProdSpec):
    koji_target = BuildParam("koji_target")
    kojiroot = BuildParam("kojiroot")
    kojihub = BuildParam("kojihub")
    image_tag = BuildParam("image_tag")

    def __init__(self):
        super(ProdSpec, self).__init__()
        self.required_params += [
            self.koji_target,
            self.kojiroot,
            self.kojihub,
        ]

    def set_params(self, koji_target=None, kojiroot=None, kojihub=None, **kwargs):
        super(ProdSpec, self).set_params(**kwargs)
        self.koji_target.value = koji_target
        self.kojiroot.value = kojiroot
        self.kojihub.value = kojihub
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.image_tag.value = "%s/%s:%s-%s" % (
            self.user.value,
            self.component.value,
            self.koji_target.value,
            timestamp
        )


class ProdWithoutKojiSpec(CommonProdSpec):
    image_tag = BuildParam("image_tag")

    def __init__(self):
        super(ProdWithoutKojiSpec, self).__init__()

    def set_params(self, **kwargs):
        super(ProdWithoutKojiSpec, self).set_params(**kwargs)
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.image_tag.value = "%s/%s:%s" % (  # FIXME: improve tag
            self.user.value,
            self.component.value,
            timestamp
        )


class ProdWithSecretSpec(ProdSpec):
    source_secret = BuildParam("source_secret")
    pulp_registry = BuildParam("pulp_registry")
    nfs_server_path = BuildParam("nfs_server_path")
    nfs_dest_dir = BuildParam("nfs_dest_dir")

    def __init__(self):
        super(ProdWithSecretSpec, self).__init__()
        self.required_params += [
            self.source_secret,
            self.pulp_registry,
            self.nfs_server_path,
        ]

    def set_params(self, source_secret=None, pulp_registry=None, nfs_server_path=None,
                   nfs_dest_dir=None, **kwargs):
        super(ProdWithSecretSpec, self).set_params(**kwargs)
        self.source_secret.value = source_secret
        self.pulp_registry.value = pulp_registry
        self.nfs_server_path.value = nfs_server_path
        self.nfs_dest_dir.value = nfs_dest_dir


class SimpleSpec(CommonSpec):
    image_tag = BuildParam("image_tag")

    def set_params(self, **kwargs):
        super(SimpleSpec, self).set_params(**kwargs)
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
        self.image_tag.value = "%s/%s:%s" % (self.user.value, self.component.value, timestamp)
