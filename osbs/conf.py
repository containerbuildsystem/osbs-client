"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import logging
import os

try:
    # py2
    import ConfigParser as configparser
    from urlparse import urljoin
except ImportError:
    # py3
    import configparser
    from urllib.parse import urljoin

from osbs.constants import DEFAULT_CONFIGURATION_FILE, DEFAULT_CONFIGURATION_SECTION, GENERAL_CONFIGURATION_SECTION
from osbs.exceptions import OsbsException


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

    def _get_value(self, args_key, conf_section, conf_key, can_miss=False, default=None, is_bool_val=False):
        # FIXME: this is too bloated: split it into separate classes
        # and implement it as mixins
        def get_value_from_kwargs():
            return self.kwargs.get(args_key, None)

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
        else:  # we didn't breaked
            if can_miss:
                return default
            raise OsbsException("value '%s' not found" % args_key)

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

        :return: list, ints representing version parts, most significant first
        """
        verstring = self._get_value("openshift_required_version",
                                    GENERAL_CONFIGURATION_SECTION,
                                    "openshift_required_version",
                                    can_miss=True)
        if verstring:
            try:
                openshift_required_version = [int(x)
                                              for x in verstring.split('.')]
            except ValueError:
                pass

            return openshift_required_version

        return None

    def get_openshift_base_uri(self):
        """
        https://<host>[:<port>]/

        :return: str
        """
        return self._get_value("openshift_uri", self.conf_section, "openshift_uri")

    @staticmethod
    def get_openshift_api_version():
        # This is not configurable.
        return "v1"

    def get_openshift_api_uri(self):
        """
        https://<host>[:<port>]/oapi/<API version>/

        :return: str
        """
        base_uri = self.get_openshift_base_uri()
        version = self.get_openshift_api_version()
        return urljoin(base_uri, "/oapi/{version}/".format(version=version))

    def get_openshift_oauth_api_uri(self):
        """
        https://<host>[:<port>]/oauth/authorize/

        :return: str
        """
        base_uri = self.get_openshift_base_uri()
        return urljoin(base_uri, "/oauth/authorize")  # MUST NOT END WITH SLASH

    def get_verbosity(self):
        val = self._get_value("verbose", GENERAL_CONFIGURATION_SECTION, "verbose", can_miss=True, is_bool_val=True)
        return val

    def get_git_uri(self):
        val = self._get_value("git_url", self.conf_section, "git_url", can_miss=True)
        return val

    def get_git_ref(self):
        val = self._get_value("git_commit", self.conf_section, "git_commit", can_miss=True)
        return val

    def get_git_branch(self):
        val = self._get_value("git_branch", self.conf_section, "git_branch", can_miss=True)
        return val

    def get_user(self):
        """ user namespace when tagging and pushing image """
        val = self._get_value("user", self.conf_section, "user", can_miss=True)
        return val

    def get_component(self):
        val = self._get_value("component", self.conf_section, "component", can_miss=True)
        return val

    def get_yum_repourls(self):
        val = self._get_value("yum_repourls", self.conf_section, "yum_repourls", can_miss=True)
        return val

    def get_namespace(self):
        val = self._get_value("namespace", self.conf_section, "namespace", can_miss=True)
        return val

    def get_kojiroot(self):
        return self._get_value("koji_root", self.conf_section, "koji_root", can_miss=True)

    def get_kojihub(self):
        return self._get_value("koji_hub", self.conf_section, "koji_hub", can_miss=True)

    def get_koji_target(self):
        val = self._get_value("target", self.conf_section, "target", can_miss=True)
        return val

    def get_sources_command(self):
        return self._get_value("sources_command", self.conf_section, "sources_command", can_miss=True)

    def get_username(self):
        return self._get_value("username", self.conf_section, "username", can_miss=True)

    def get_password(self):
        return self._get_value("password", self.conf_section, "password", can_miss=True)

    def get_client_cert(self):
        return self._get_value("client_cert", self.conf_section, "client_cert", can_miss=True)

    def get_client_key(self):
        return self._get_value("client_key", self.conf_section, "client_key", can_miss=True)

    def get_use_kerberos(self):
        return self._get_value("use_kerberos", self.conf_section, "use_kerberos", can_miss=True, is_bool_val=True)

    def get_kerberos_keytab(self):
        return self._get_value("kerberos_keytab", self.conf_section, "kerberos_keytab", can_miss=True)

    def get_kerberos_principal(self):
        return self._get_value("kerberos_principal", self.conf_section, "kerberos_principal", can_miss=True)

    def get_kerberos_ccache(self):
        return self._get_value("kerberos_ccache", self.conf_section, "kerberos_ccache", can_miss=True)

    def get_registry_uri(self):
        return self._get_value("registry_uri", self.conf_section, "registry_uri", can_miss=True)

    def get_pulp_registry(self):
        return self._get_value("pulp_registry_name", self.conf_section, "pulp_registry_name", can_miss=True)

    def get_build_json_store(self):
        return self._get_value("build_json_dir", GENERAL_CONFIGURATION_SECTION, "build_json_dir")

    def get_verify_ssl(self):
        return self._get_value("verify_ssl", self.conf_section, "verify_ssl",
                               default=True, can_miss=True, is_bool_val=True)

    def get_build_type(self):
        return self._get_value("build_type", self.conf_section, "build_type")

    def get_vendor(self):
        return self._get_value("vendor", self.conf_section, "vendor", can_miss=True)

    def get_build_host(self):
        return self._get_value("build_host", self.conf_section, "build_host", can_miss=True)

    def get_authoritative_registry(self):
        return self._get_value("authoritative_registry", self.conf_section, "authoritative_registry", can_miss=True)

    def get_architecture(self):
        return self._get_value("arch", self.conf_section, "architecture", can_miss=True)

    def get_use_auth(self):
        return self._get_value("use_auth", self.conf_section, "use_auth", can_miss=True, is_bool_val=True)

    def get_pulp_secret(self):
        secret = self._get_value("pulp_secret", self.conf_section,
                                 "pulp_secret", can_miss=True)
        if not secret:
            secret = self._get_value("source_secret", self.conf_section,
                                     "pulp_secret", can_miss=True)
        return secret

    def get_source_secret(self):
        """
        Compatibility name for get_pulp_secret()
        """
        return self.get_pulp_secret()

    def get_nfs_server_path(self):
        return self._get_value("nfs_server_path", self.conf_section,
                               "nfs_server_path", can_miss=True)

    def get_nfs_destination_dir(self):
        return self._get_value("nfs_dest_dir", self.conf_section,
                               "nfs_dest_dir", can_miss=True)

    def get_cpu_limit(self):
        return self._get_value("cpu_limit", self.conf_section,
                               "cpu_limit", can_miss=True)

    def get_memory_limit(self):
        return self._get_value("memory_limit", self.conf_section,
                               "memory_limit", can_miss=True)

    def get_storage_limit(self):
        return self._get_value("storage_limit", self.conf_section,
                               "storage_limit", can_miss=True)

    def get_git_push_url(self):
        return self._get_value("git_push_url", self.conf_section,
                               "git_push_url", can_miss=True)

    def get_git_push_username(self):
        return self._get_value("git_push_username", self.conf_section,
                               "git_push_username", can_miss=True)
