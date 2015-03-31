try:
    # py2
    import ConfigParser as configparser
    from urlparse import urljoin
except ImportError:
    # py3
    import configparser
    from urllib.parse import urljoin

from osbs.constants import DEFAULT_CONFIGURATION_FILE, DEFAULT_CONFIGURATION_SECTION, GENERAL_CONFIGURATION_SECTION


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

        :param conf_file: str, path to configuration file
        :param conf_section: str, name of section with configuration for requested instance
        :param cli_args: instance of argument parser of argparse
        :param kwargs: keyword arguments, which have highest priority: key is cli argument name
        """
        self.scp = configparser.SafeConfigParser()
        try:
            self.scp.read(conf_file)
        except IOError:
            pass
        else:
            if not self.scp.has_section(conf_section):
                raise RuntimeError("Specified section '%s' not found in '%s'" % (conf_section, conf_file))
        self.conf_section = conf_section
        self.args = cli_args
        self.kwargs = kwargs

    def _get_value(self, args_key, conf_section, conf_key, can_miss=False, default=None):
        # FIXME: this is too bloated: split it into separate classes
        # and implement it as mixins
        def get_value_from_kwargs():
            try:
                return self.kwargs[args_key]
            except KeyError:
                pass

        def get_value_from_cli_args():
            try:
                return getattr(self.args, args_key, None)
            except AttributeError:
                pass

        def get_value_from_conf():
                try:
                    return self.scp.get(conf_section, conf_key)
                except configparser.Error:
                    pass

        retrieval_order = [
            get_value_from_kwargs,
            get_value_from_cli_args,
            get_value_from_conf,
        ]
        for func in retrieval_order:
            value = func()
            if value is not None:
                return value
        if can_miss:
            return default
        raise RuntimeError("value '%s' not found" % args_key)

    def get_openshift_base_uri(self):
        """
        https://<host>[:<port>]/

        :return: str
        """
        return self._get_value("openshift_uri", self.conf_section, "openshift_uri")

    def get_openshift_api_uri(self):
        """
        https://<host>[:<port>]/osapi/v<number>beta<number>/

        :return: str
        """
        base_uri = self.get_openshift_base_uri()
        return urljoin(base_uri, "/osapi/v1beta1/")

    def get_openshift_oauth_api_uri(self):
        """
        https://<host>[:<port>]/oauth/authorize/

        :return: str
        """
        base_uri = self.get_openshift_base_uri()
        return urljoin(base_uri, "/oauth/authorize")  # MUST NOT END WITH SLASH

    def get_kubelet_uri(self):
        return self._get_value("kubelet_uri", self.conf_section, "kubelet_uri")

    def get_verbosity(self):
        val = self._get_value("verbose", GENERAL_CONFIGURATION_SECTION, "verbose")
        try:
            int_val = int(val)
        except ValueError:
            if val.lower() == 'true':
                return True
            return False
        else:
            return bool(int_val)

    def get_kojiroot(self):
        return self._get_value("koji_root", self.conf_section, "koji_root")

    def get_kojihub(self):
        return self._get_value("koji_hub", self.conf_section, "koji_hub")

    def get_sources_command(self):
        return self._get_value("sources_command", self.conf_section, "sources_command")

    def get_username(self):
        return self._get_value("username", self.conf_section, "username", can_miss=True)

    def get_password(self):
        return self._get_value("password", self.conf_section, "password", can_miss=True)

    def get_use_kerberos(self):
        return self._get_value("use_kerberos", self.conf_section, "use_kerberos", can_miss=True)

    def get_registry_uri(self):
        return self._get_value("registry_uri", self.conf_section, "registry_uri")

    def get_build_json_store(self):
        return self._get_value("build_json_dir", self.conf_section, "build_json_dir")

    def get_verify_ssl(self):
        return self._get_value("verify_ssl", self.conf_section, "verify_ssl", default=True, can_miss=True)
