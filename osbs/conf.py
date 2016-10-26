"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import os
import os.path
import warnings
from pkg_resources import parse_version

try:
    # py2
    import ConfigParser as configparser
    from urlparse import urljoin
except ImportError:
    # py3
    import configparser
    from urllib.parse import urljoin

from osbs.constants import (DEFAULT_CONFIGURATION_FILE, DEFAULT_CONFIGURATION_SECTION,
                            GENERAL_CONFIGURATION_SECTION, DEFAULT_NAMESPACE)
from osbs import utils


logger = logging.getLogger(__name__)


class Configuration(object):
    """
    class for managing configuration; it takes data from

     * ini-style config
     * command line (argparse)
     * dict
    """

    def __init__(self, conf_file=DEFAULT_CONFIGURATION_FILE, conf_section=DEFAULT_CONFIGURATION_SECTION,
                 cli_args=None, **kwargs):
        """
        sample initialization:

            Configuration("./osbs.conf", "fedora", openshift_uri="https://localhost:8443/",
                          username="admin", password="something")

        :param conf_file: str, path to configuration file, or None for no configuration file
        :param conf_section: str, name of section with configuration for requested instance
        :param cli_args: instance of argument parser of argparse
        :param kwargs: keyword arguments, which have highest priority: key is cli argument name
        """
        self.scp = configparser.SafeConfigParser()
        if conf_file and os.path.isfile(conf_file) and os.access(conf_file, os.R_OK):
            self.scp.read(conf_file)
            if not self.scp.has_section(conf_section):
                logger.warning("Specified section '%s' not found in '%s'",
                               conf_section, conf_file)
        self.conf_section = conf_section
        self.args = cli_args
        self.kwargs = kwargs

    def _get_value(self, args_key, conf_section, conf_key, default=None, is_bool_val=False):
        # FIXME: this is too bloated: split it into separate classes
        # and implement it as mixins
        def get_value_from_kwargs():
            return self.kwargs.get(args_key)

        def get_value_from_cli_args():
            return getattr(self.args, args_key, None)

        def get_value_from_conf():
            try:
                return self.scp.get(conf_section, conf_key)
            except configparser.Error:
                return None

        retrieval_order = [
            get_value_from_kwargs,
            get_value_from_cli_args,
            get_value_from_conf,
        ]

        for func in retrieval_order:
            value = func()
            if value is not None:
                break
        else:  # we didn't break
            return default

        if is_bool_val:
            try:
                int_val = int(value)
            except ValueError:
                if value.lower() == 'true':
                    return True
                return False
            except TypeError:
                return False
            else:
                return bool(int_val)
        else:
            return value

    def get_openshift_required_version(self):
        """
        Get minimum version of openshift we require

        :return: None, or else an object instance that allows comparisons
        """
        verstring = self._get_value("openshift_required_version",
                                    GENERAL_CONFIGURATION_SECTION,
                                    "openshift_required_version")
        if verstring:
            return parse_version(verstring)

        return None

    def get_openshift_base_uri(self):
        """
        https://<host>[:<port>]/

        :return: str
        """
        deprecated_key = "openshift_uri"
        key = "openshift_url"
        val = self._get_value(deprecated_key, self.conf_section, deprecated_key)
        if val is not None:
            warnings.warn("%r is deprecated, use %r instead" % (deprecated_key, key))
            return val
        return self._get_value(key, self.conf_section, key)

    @staticmethod
    def get_openshift_api_version():
        # This is not configurable.
        return "v1"

    def _get_api_uri(self, keyword):
        base_uri = self.get_openshift_base_uri()
        version = self.get_openshift_api_version()
        return urljoin(base_uri,
                       "/{keyword}/{version}/".format(keyword=keyword,
                                                      version=version))

    def get_k8s_api_uri(self):
        """
        https://<host>[:<port>]/api/<API version>/

        :return: str
        """
        return self._get_api_uri('api')

    def get_openshift_api_uri(self):
        """
        https://<host>[:<port>]/oapi/<API version>/

        :return: str
        """
        return self._get_api_uri('oapi')

    def get_openshift_oauth_api_uri(self):
        """
        https://<host>[:<port>]/oauth/authorize/

        :return: str
        """
        base_uri = self.get_openshift_base_uri()
        return urljoin(base_uri, "/oauth/authorize")  # MUST NOT END WITH SLASH

    def get_verbosity(self):
        return self._get_value("verbose", GENERAL_CONFIGURATION_SECTION, "verbose",
                               is_bool_val=True)

    def get_git_uri(self):
        return self._get_value("git_url", self.conf_section, "git_url")

    def get_git_ref(self):
        return self._get_value("git_commit", self.conf_section, "git_commit")

    def get_git_branch(self):
        return self._get_value("git_branch", self.conf_section, "git_branch")

    def get_user(self):
        """ user namespace when tagging and pushing image """
        return self._get_value("user", self.conf_section, "user")

    def get_component(self):
        return self._get_value("component", self.conf_section, "component")

    def get_tag(self):
        return self._get_value("tag", self.conf_section, "tag")

    def get_yum_repourls(self):
        return self._get_value("yum_repourls", self.conf_section, "yum_repourls")

    def get_namespace(self):
        return self._get_value("namespace", self.conf_section, "namespace",
                               default=DEFAULT_NAMESPACE)

    def get_kojiroot(self):
        return self._get_value("koji_root", self.conf_section, "koji_root")

    def get_kojihub(self):
        return self._get_value("koji_hub", self.conf_section, "koji_hub")

    def get_koji_target(self):
        return self._get_value("target", self.conf_section, "target")

    def get_koji_certs_secret(self):
        return self._get_value("koji_certs_secret", self.conf_section, "koji_certs_secret")

    def get_sources_command(self):
        return self._get_value("sources_command", self.conf_section, "sources_command")

    def get_username(self):
        return self._get_value("username", self.conf_section, "username")

    def get_password(self):
        return self._get_value("password", self.conf_section, "password")

    def get_client_cert(self):
        return self._get_value("client_cert", self.conf_section, "client_cert")

    def get_client_key(self):
        return self._get_value("client_key", self.conf_section, "client_key")

    def get_use_kerberos(self):
        return self._get_value("use_kerberos", self.conf_section, "use_kerberos", is_bool_val=True)

    def get_kerberos_keytab(self):
        return self._get_value("kerberos_keytab", self.conf_section, "kerberos_keytab")

    def get_kerberos_principal(self):
        return self._get_value("kerberos_principal", self.conf_section, "kerberos_principal")

    def get_kerberos_ccache(self):
        return self._get_value("kerberos_ccache", self.conf_section, "kerberos_ccache")

    def get_registry_uris(self):
        value = self._get_value("registry_uri",
                                self.conf_section,
                                "registry_uri")
        if value:
            return value.split(',')
        else:
            return []

    def get_registry_secrets(self):
        value = self._get_value("registry_secret", self.conf_section, "registry_secret")
        if value:
            return value.split(',')
        else:
            return []

    def get_registry_api_versions(self):
        value = self._get_value("registry_api_versions",
                                self.conf_section,
                                "registry_api_versions",
                                default='v1,v2')
        return value.split(',')

    def get_source_registry_uri(self):
        return self._get_value("source_registry_uri", self.conf_section, "source_registry_uri")

    def get_pulp_registry(self):
        return self._get_value("pulp_registry_name", self.conf_section, "pulp_registry_name")

    def get_build_json_store(self):
        return self._get_value("build_json_dir", GENERAL_CONFIGURATION_SECTION, "build_json_dir")

    def get_verify_ssl(self):
        return self._get_value("verify_ssl", self.conf_section, "verify_ssl",
                               default=True, is_bool_val=True)

    def get_vendor(self):
        return self._get_value("vendor", self.conf_section, "vendor")

    def get_build_host(self):
        return self._get_value("build_host", self.conf_section, "build_host")

    def get_authoritative_registry(self):
        return self._get_value("authoritative_registry", self.conf_section,
                               "authoritative_registry")

    def get_distribution_scope(self):
        return self._get_value("distribution_scope", self.conf_section, "distribution_scope")

    def get_architecture(self):
        return self._get_value("arch", self.conf_section, "architecture")

    def get_use_auth(self):
        return self._get_value("use_auth", self.conf_section, "use_auth", is_bool_val=True)

    def get_builder_use_auth(self):
        return self._get_value("builder_use_auth", self.conf_section,
                               "builder_use_auth",
                               default=self.get_use_auth(),
                               is_bool_val=True)

    def get_builder_openshift_url(self):
        """ url of OpenShift where builder will connect """
        key = "builder_openshift_url"
        url = self._get_value(key, self.conf_section, key)
        if url is None:
            logger.warning("%r not found, falling back to get_openshift_base_uri()", key)
            url = self.get_openshift_base_uri()
        return url

    def get_builder_build_json_store(self):
        key = "builder_build_json_dir"
        builder_build_json_dir = self._get_value(key, self.conf_section, key)
        if builder_build_json_dir is None:
            fallback_key = "build_json_dir"
            logger.warning("%r not found, falling back %r", key, fallback_key)
            builder_build_json_dir = self._get_value(fallback_key, self.conf_section, fallback_key)
        return builder_build_json_dir

    def get_pulp_secret(self):
        secret = self._get_value("pulp_secret", self.conf_section, "pulp_secret")
        if not secret:
            secret = self._get_value("source_secret", self.conf_section, "pulp_secret")
        return secret

    def get_source_secret(self):
        """
        Compatibility name for get_pulp_secret()
        """
        return self.get_pulp_secret()

    def get_pdc_secret(self):
        return self._get_value("pdc_secret", self.conf_section, "pdc_secret")

    def get_pdc_url(self):
        return self._get_value("pdc_url", self.conf_section, "pdc_url")

    def get_smtp_uri(self):
        return self._get_value("smtp_uri", self.conf_section, "smtp_uri")

    def get_nfs_server_path(self):
        return self._get_value("nfs_server_path", self.conf_section, "nfs_server_path")

    def get_nfs_destination_dir(self):
        return self._get_value("nfs_dest_dir", self.conf_section, "nfs_dest_dir")

    def get_cpu_limit(self):
        return self._get_value("cpu_limit", self.conf_section, "cpu_limit")

    def get_memory_limit(self):
        return self._get_value("memory_limit", self.conf_section, "memory_limit")

    def get_storage_limit(self):
        return self._get_value("storage_limit", self.conf_section, "storage_limit")

    def get_git_push_url(self):
        return self._get_value("git_push_url", self.conf_section, "git_push_url")

    def get_git_push_username(self):
        return self._get_value("git_push_username", self.conf_section, "git_push_username")

    def get_build_image(self):
        return self._get_value("build_image", self.conf_section, "build_image")

    def get_build_imagestream(self):
        return self._get_value("build_imagestream", self.conf_section, "build_imagestream")

    def get_proxy(self):
        return self._get_value("yum_proxy", self.conf_section, "yum_proxy")

    def get_scratch(self, default_value):
        return self._get_value("scratch", self.conf_section, "scratch",
                               default=default_value, is_bool_val=True)

    def get_unique_tag_only(self):
        return self._get_value("unique_tag_only", self.conf_section, "unique_tag_only",
                               default=False, is_bool_val=True)

    def get_oauth2_token(self):
        # token overrides token_file
        # either in kwargs overrides cli args
        # either in cli args overrides conf
        key_names = ['token', 'token_file']
        value = None
        found_key = None
        for key in key_names:
            value = self.kwargs.get(key)
            if value is not None:
                found_key = key
                break

        if value is None:
            for key in key_names:
                value = getattr(self.args, key, None)
                if value is not None:
                    found_key = key
                    break

        if value is None:
            for key in key_names:
                try:
                    value = self.scp.get(self.conf_section, key)
                except configparser.Error:
                    pass
                else:
                    found_key = key
                    break

        if value is None:
            instance_token_file = utils.get_instance_token_file_name(self.conf_section)
            if os.path.exists(instance_token_file):
                found_key = 'token_file'
                value = instance_token_file

        # For token_file, read the file
        if found_key == 'token_file':
            token_file = value
            try:
                with open(token_file, 'r') as token_fd:
                    value = token_fd.read().strip()
            except IOError as ex:
                logger.error("exception caught while reading %s: %r",
                             token_file, ex)

        return value
