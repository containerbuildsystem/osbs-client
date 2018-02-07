"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import os
import re
import random
from osbs.constants import DEFAULT_GIT_REF, DEFAULT_BUILD_IMAGE, RAND_DIGITS
from osbs.exceptions import OsbsValidationException
from osbs.utils import (get_imagestreamtag_from_image,
                        make_name_from_git,
                        RegistryURI, utcnow)

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

    Each registry has a full URI, a docker URI, and a version (str).
    """

    name = "registry_uris"

    def __init__(self):
        super(RegistryURIsParam, self).__init__(self.name)

    @BuildParam.value.setter
    def value(self, val):  # pylint: disable=W0221
        registry_uris = [RegistryURI(uri) for uri in val]
        BuildParam.value.fset(self, registry_uris)


class SourceRegistryURIParam(BuildParam):
    name = "source_registry_uri"

    def __init__(self):
        super(SourceRegistryURIParam, self).__init__(self.name)

    @BuildParam.value.setter
    def value(self, val):  # pylint: disable=W0221
        BuildParam.value.fset(self, RegistryURI(val) if val else None)


class BuildSpec(object):

    def __init__(self):

        self.git_uri = BuildParam('git_uri')
        self.git_ref = BuildParam('git_ref', default=DEFAULT_GIT_REF)
        self.git_branch = BuildParam('git_branch')
        self.user = UserParam()
        self.component = BuildParam('component')
        self.registry_uris = RegistryURIsParam()
        self.registry_secrets = BuildParam('registry_secrets', allow_none=True)
        self.source_registry_uri = SourceRegistryURIParam()
        self.openshift_uri = BuildParam('openshift_uri')
        self.builder_openshift_url = BuildParam('builder_openshift_url')
        self.name = BuildIDParam()
        self.yum_repourls = BuildParam("yum_repourls")
        self.use_auth = BuildParam("use_auth", allow_none=True)
        self.build_image = BuildParam('build_image')
        self.build_imagestream = BuildParam('build_imagestream')
        self.proxy = BuildParam("proxy", allow_none=True)
        self.trigger_imagestreamtag = BuildParam('trigger_imagestreamtag')
        self.imagestream_name = BuildParam('imagestream_name')
        self.imagestream_url = BuildParam('imagestream_url')
        self.imagestream_insecure_registry = BuildParam('imagestream_insecure_registry')
        self.sources_command = BuildParam("sources_command", allow_none=True)
        self.architecture = BuildParam("architecture")
        self.vendor = BuildParam("vendor", allow_none=True)
        self.build_host = BuildParam("build_host")
        self.authoritative_registry = BuildParam("authoritative_registry", allow_none=True)
        self.distribution_scope = BuildParam("distribution_scope", allow_none=True)
        self.registry_api_versions = BuildParam("registry_api_versions")
        self.koji_target = BuildParam("koji_target", allow_none=True)
        self.kojiroot = BuildParam("kojiroot", allow_none=True)
        self.kojihub = BuildParam("kojihub", allow_none=True)
        self.koji_certs_secret = BuildParam("koji_certs_secret", allow_none=True)
        self.koji_task_id = BuildParam("koji_task_id", allow_none=True)
        self.filesystem_koji_task_id = BuildParam("filesystem_koji_task_id", allow_none=True)
        self.koji_use_kerberos = BuildParam("koji_use_kerberos", allow_none=True)
        self.koji_kerberos_principal = BuildParam("koji_kerberos_principal", allow_none=True)
        self.koji_kerberos_keytab = BuildParam("koji_kerberos_keytab", allow_none=True)
        self.flatpak = BuildParam("flatpak", default=False)
        self.module = BuildParam("module", allow_none=True)
        self.module_compose_id = BuildParam("module_compose_id", allow_none=True)
        self.flatpak_base_image = BuildParam("flatpak_base_image", allow_none=True)
        self.odcs_url = BuildParam("odcs_url", allow_none=True)
        self.odcs_insecure = BuildParam("odcs_insecure", allow_none=True)
        self.odcs_openidc_secret = BuildParam("odcs_openidc_secret", allow_none=True)
        self.odcs_ssl_secret = BuildParam("odcs_ssl_secret", allow_none=True)
        self.pdc_url = BuildParam("pdc_url", allow_none=True)
        self.pdc_insecure = BuildParam("pdc_insecure", allow_none=True)
        self.image_tag = BuildParam("image_tag")
        self.pulp_secret = BuildParam("pulp_secret", allow_none=True)
        self.pulp_registry = BuildParam("pulp_registry", allow_none=True)
        self.smtp_host = BuildParam("smtp_host", allow_none=True)
        self.smtp_from = BuildParam("smtp_from", allow_none=True)
        self.smtp_additional_addresses = BuildParam("smtp_additional_addresses", allow_none=True)
        self.smtp_error_addresses = BuildParam("smtp_error_addresses", allow_none=True)
        self.smtp_email_domain = BuildParam("smtp_email_domain", allow_none=True)
        self.smtp_to_submitter = BuildParam("smtp_to_submitter", allow_none=True)
        self.smtp_to_pkgowner = BuildParam("smtp_to_pkgowner", allow_none=True)
        self.builder_build_json_dir = BuildParam("builder_build_json_dir", allow_none=True)
        self.platforms = BuildParam("platforms", allow_none=True)
        self.platform = BuildParam("platform", allow_none=True)
        self.build_type = BuildParam("build_type", allow_none=True)
        self.release = BuildParam("release", allow_none=True)
        self.reactor_config_secret = BuildParam("reactor_config_secret", allow_none=True)
        self.reactor_config_map = BuildParam("reactor_config_map", allow_none=True)
        self.client_config_secret = BuildParam("client_config_secret", allow_none=True)
        self.token_secrets = BuildParam("token_secrets", allow_none=True)
        self.arrangement_version = BuildParam("arrangement_version", allow_none=True)
        self.info_url_format = BuildParam("info_url_format", allow_none=True)
        self.artifacts_allowed_domains = BuildParam("artifacts_allowed_domains", allow_none=True)
        self.equal_labels = BuildParam("equal_labels", allow_none=True)
        self.koji_upload_dir = BuildParam("koji_upload_dir", allow_none=True)
        self.yum_proxy = BuildParam("yum_proxy", allow_none=True)
        self.koji_parent_build = BuildParam("koji_parent_build", allow_none=True)
        self.group_manifests = BuildParam("group_manifests", allow_none=True)
        self.prefer_schema1_digest = BuildParam("prefer_schema1_digest", allow_none=True)
        self.signing_intent = BuildParam("signing_intent", allow_none=True)
        self.compose_ids = BuildParam("compose_ids", allow_none=True)

        self.required_params = [
            self.git_uri,
            self.git_ref,
            self.user,
            self.registry_uris,
            self.openshift_uri,
            self.sources_command,
            self.vendor,
            self.authoritative_registry,
            self.distribution_scope,
            self.registry_api_versions,
            self.koji_target,
            self.kojiroot,
            self.kojihub,
            self.koji_certs_secret,
            self.pulp_secret,
            self.pulp_registry,
            self.smtp_host,
            self.smtp_from,
        ]

    def set_params(self, git_uri=None, git_ref=None,
                   registry_uri=None,  # compatibility name for registry_uris
                   registry_uris=None, registry_secrets=None,
                   user=None,
                   component=None, openshift_uri=None, source_registry_uri=None,
                   yum_repourls=None, use_auth=None, builder_openshift_url=None,
                   build_image=None, build_imagestream=None, proxy=None,
                   sources_command=None, architecture=None, vendor=None,
                   build_host=None, authoritative_registry=None, distribution_scope=None,
                   koji_target=None, kojiroot=None, kojihub=None, koji_certs_secret=None,
                   koji_use_kerberos=None, koji_kerberos_keytab=None,
                   koji_kerberos_principal=None, koji_task_id=None,
                   flatpak=False,
                   module=None, module_compose_id=None,
                   flatpak_base_image=None,
                   odcs_url=None, odcs_insecure=False, odcs_openidc_secret=None,
                   odcs_ssl_secret=None,
                   pdc_url=None, pdc_insecure=False,
                   filesystem_koji_task_id=None,
                   source_secret=None,  # compatibility name for pulp_secret
                   pulp_secret=None, pulp_registry=None,
                   smtp_host=None, smtp_from=None, smtp_email_domain=None,
                   smtp_additional_addresses=None, smtp_error_addresses=None,
                   smtp_to_submitter=None, smtp_to_pkgowner=None,
                   git_branch=None, base_image=None,
                   name_label=None,
                   builder_build_json_dir=None, registry_api_versions=None,
                   platforms=None, platform=None, build_type=None, release=None,
                   reactor_config_secret=None, reactor_config_map=None,
                   client_config_secret=None,
                   token_secrets=None, arrangement_version=None,
                   info_url_format=None, artifacts_allowed_domains=None,
                   equal_labels=None, koji_upload_dir=None, yum_proxy=None,
                   koji_parent_build=None, group_manifests=None, prefer_schema1_digest=None,
                   signing_intent=None, compose_ids=None,
                   **kwargs):
        self.git_uri.value = git_uri
        self.git_ref.value = git_ref
        self.user.value = user
        self.component.value = component
        self.proxy.value = proxy

        # registry_uri is the compatibility name for registry_uris
        if registry_uri is not None:
            assert registry_uris is None
            registry_uris = [registry_uri]

        self.registry_uris.value = registry_uris or []
        self.registry_secrets.value = registry_secrets or []
        self.source_registry_uri.value = source_registry_uri
        self.openshift_uri.value = openshift_uri
        self.builder_openshift_url.value = builder_openshift_url
        if not (yum_repourls is None or isinstance(yum_repourls, list)):
            raise OsbsValidationException("yum_repourls must be a list")
        self.yum_repourls.value = yum_repourls or []
        self.use_auth.value = use_auth

        if build_imagestream and build_image:
            raise OsbsValidationException(
                'Please only define build_image -OR- build_imagestream, not both')
        self.build_image.value = build_image or DEFAULT_BUILD_IMAGE
        self.build_imagestream.value = build_imagestream

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
        self.koji_certs_secret.value = koji_certs_secret
        self.koji_use_kerberos.value = koji_use_kerberos
        self.koji_kerberos_principal.value = koji_kerberos_principal
        self.koji_kerberos_keytab.value = koji_kerberos_keytab
        self.koji_task_id.value = koji_task_id
        self.flatpak.value = flatpak
        self.module.value = module
        self.module_compose_id.value = module_compose_id
        self.flatpak_base_image.value = flatpak_base_image
        self.odcs_url.value = odcs_url
        self.odcs_insecure.value = odcs_insecure
        self.odcs_openidc_secret.value = odcs_openidc_secret
        self.odcs_ssl_secret.value = odcs_ssl_secret
        self.pdc_url.value = pdc_url
        self.pdc_insecure.value = pdc_insecure
        self.pulp_secret.value = pulp_secret or source_secret
        self.pulp_registry.value = pulp_registry
        self.smtp_host.value = smtp_host
        self.smtp_from.value = smtp_from
        self.smtp_additional_addresses.value = smtp_additional_addresses
        self.smtp_error_addresses.value = smtp_error_addresses
        self.smtp_email_domain.value = smtp_email_domain
        self.smtp_to_submitter.value = smtp_to_submitter
        self.smtp_to_pkgowner.value = smtp_to_pkgowner
        self.git_branch.value = git_branch
        self.name.value = make_name_from_git(self.git_uri.value, self.git_branch.value)
        self.group_manifests.value = group_manifests or False
        self.prefer_schema1_digest.value = prefer_schema1_digest
        self.builder_build_json_dir.value = builder_build_json_dir

        if not flatpak:
            if not base_image:
                raise OsbsValidationException("base_image must be provided")
            self.trigger_imagestreamtag.value = get_imagestreamtag_from_image(base_image)

            if not name_label:
                raise OsbsValidationException("name_label must be provided")
            self.imagestream_name.value = name_label.replace('/', '-')
            # The ImageStream should take tags from the source registry
            # or, if no source registry is set, the first listed registry
            imagestream_reg = self.source_registry_uri.value
            if not imagestream_reg:
                try:
                    imagestream_reg = self.registry_uris.value[0]
                except IndexError:
                    logger.info("no registries specified, cannot determine imagestream url")
                    imagestream_reg = None

            if imagestream_reg:
                self.imagestream_url.value = os.path.join(imagestream_reg.docker_uri,
                                                          name_label)
                logger.debug("setting 'imagestream_url' to '%s'",
                             self.imagestream_url.value)
                insecure = imagestream_reg.uri.startswith('http://')
                self.imagestream_insecure_registry.value = insecure
                logger.debug("setting 'imagestream_insecure_registry' to %r", insecure)

        self.platforms.value = platforms
        self.platform.value = platform
        self.build_type.value = build_type
        self.release.value = release
        self.reactor_config_secret.value = reactor_config_secret
        self.reactor_config_map.value = reactor_config_map
        self.client_config_secret.value = client_config_secret
        self.token_secrets.value = token_secrets or {}
        self.arrangement_version.value = arrangement_version
        self.info_url_format.value = info_url_format
        self.artifacts_allowed_domains.value = artifacts_allowed_domains
        self.equal_labels.value = equal_labels
        self.filesystem_koji_task_id.value = filesystem_koji_task_id
        self.koji_upload_dir.value = koji_upload_dir
        self.yum_proxy.value = yum_proxy
        self.koji_parent_build.value = koji_parent_build

        if (signing_intent or compose_ids) and not self.odcs_enabled():
            raise OsbsValidationException(
                'signing_intent and compose_ids are allowed only when ODCS is enabled')

        if signing_intent and compose_ids:
            raise OsbsValidationException(
                'Please only define signing_intent -OR- compose_ids, not both')

        if compose_ids and yum_repourls:
            raise OsbsValidationException(
                'Please only define yum_repourls -OR- compose_ids, not both')

        try:
            compose_ids and iter(compose_ids)
        except TypeError:
            raise OsbsValidationException("compose_ids must be a list")

        self.signing_intent.value = signing_intent
        self.compose_ids.value = compose_ids or []
        self._populate_image_tag()

    def odcs_enabled(self):
        return self.odcs_url.value

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

        # Support for platform specific tags has only been added in arrangement 4.
        if self.platform.value and (self.arrangement_version.value or 0) >= 4:
            tag_segments.append(self.platform.value)

        tag = '-'.join(tag_segments)
        self.image_tag.value = '{0}/{1}:{2}'.format(self.user.value, self.component.value, tag)

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
        return "Spec(%s)" % self.__dict__
