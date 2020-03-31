from tito.builder import Builder


class OsbsClientBuilder(Builder):

    def _get_tgz_name_and_ver(self):
        """ Returns name of tgz created by tito """
        return "%s" % self.display_version
