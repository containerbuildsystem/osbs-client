from ConfigParser import SafeConfigParser
import ConfigParser
from urlparse import urljoin
from osbs.constants import DEFAULT_CONFIGURATION_FILE


class Configuration(object):

    def __init__(self, conf_file=DEFAULT_CONFIGURATION_FILE, cli_args=None, **kwargs):
        """

        :param conf_file:
        :param args:
        :return:
        """
        self.scp = SafeConfigParser()
        try:
            self.scp.read(conf_file)
        except IOError:
            pass
        self.args = cli_args
        self.kwargs = kwargs

    def _get_value(self, args_key, conf_section, conf_key):
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
                except ConfigParser.Error:
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
        raise RuntimeError("value '%s' not found" % args_key)

    def get_openshift_base_uri(self):
        """
        https://<host>[:<port>]/

        :return: str
        """
        return self._get_value("openshift_uri", "General", "openshift_uri")

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
        return self._get_value("kubelet_uri", "General", "kubelet_uri")

    def get_verbosity(self):
        return self._get_value("verbose", "General", "verbose")

    def get_kojiroot(self):
        return self._get_value("koji_root", "General", "koji_root")

    def get_kojihub(self):
        return self._get_value("koji_hub", "General", "koji_hub")

    def get_rpkg_binary(self):
        return self._get_value("rpkg_binary", "General", "rpkg_binary")
