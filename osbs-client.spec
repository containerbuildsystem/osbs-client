%if 0%{?rhel} && 0%{?rhel} <= 7
%{!?py2_build: %global py2_build %{__python2} setup.py build}
%{!?py2_install: %global py2_install %{__python2} setup.py install --skip-build --root %{buildroot}}
%endif

%if (0%{?fedora} >= 22 || 0%{?rhel} >= 8)
%{!?with_python3:%global with_python3 1}
%global binaries_py_version %{python3_version}
%else
%global binaries_py_version %{python2_version}
%endif

%if 0%{?fedora}
# rhel/epel has older incompatible version of pytest (no caplog)
%global with_check 1
%endif

%global commit 775a6929846edd3ebd5057ae513c2b124ac3c9bd
%global shortcommit %(c=%{commit}; echo ${c:0:7})

# set to 0 to create a normal release
%global dev_release 0

%if 0%{?dev_release}
%global postrelease dev
%global release 0
%else
%global postrelease 0
%global release 1
%endif

%global osbs_obsolete_vr 0.14-2

Name:           osbs-client
Version:        0.62
%if "x%{postrelease}" != "x0"
Release:        %{release}.%{postrelease}.git.%{shortcommit}%{?dist}
%else
Release:        %{release}%{?dist}
%endif

Summary:        Python command line client for OpenShift Build Service
Group:          Development/Tools
License:        BSD
URL:            https://github.com/containerbuildsystem/osbs-client
Source0:        https://github.com/containerbuildsystem/osbs-client/archive/%{commit}/osbs-client-%{commit}.tar.gz

BuildArch:      noarch

%if 0%{?with_python3}
Requires:       python3-osbs-client = %{version}-%{release}
%else
Requires:       python-osbs-client = %{version}-%{release}
%endif

BuildRequires:  git-core
%if 0%{?with_python3}
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
%else
BuildRequires:  python2-devel
BuildRequires:  python-setuptools
%endif

%if 0%{?with_check}
%if 0%{?with_python3}
BuildRequires:  python3-dateutil
BuildRequires:  python3-pytest
BuildRequires:  python3-flexmock
BuildRequires:  python3-six
BuildRequires:  python3-dockerfile-parse
BuildRequires:  python3-requests
BuildRequires:  python3-requests-kerberos
BuildRequires:  python3-PyYAML
%else
BuildRequires:  pytest
BuildRequires:  python-flexmock
BuildRequires:  python-six
BuildRequires:  python-dockerfile-parse
BuildRequires:  python-requests
BuildRequires:  python-requests-kerberos
BuildRequires:  PyYAML
%endif # with_python3
%endif # with_check


Provides:       osbs = %{version}-%{release}
Obsoletes:      osbs < %{osbs_obsolete_vr}

%description
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs command line client.

%if 0%{?with_python3}
%package -n python3-osbs-client
Summary:        Python 3 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python3-dockerfile-parse
Requires:       python3-requests
Requires:       python3-requests-kerberos
Requires:       python3-dateutil
Requires:       python3-setuptools
Requires:       python3-six
Requires:       krb5-workstation
Requires:       python3-PyYAML
Requires:       git-core

Provides:       python3-osbs = %{version}-%{release}
Obsoletes:      python3-osbs < %{osbs_obsolete_vr}
%{?python_provide:%python_provide python3-osbs-client}

%description -n python3-osbs-client
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs Python 3 bindings.

%else
%package -n python-osbs-client
Summary:        Python 2 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python-dockerfile-parse
Requires:       python-requests
Requires:       python-requests-kerberos
Requires:       python-setuptools
Requires:       python-six
Requires:       krb5-workstation
Requires:       PyYAML
Requires:       git-core

Provides:       python-osbs = %{version}-%{release}
Obsoletes:      python-osbs < %{osbs_obsolete_vr}
%{?python_provide:%python_provide python-osbs-client}

%description -n python-osbs-client
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs Python 2 bindings.
%endif # with_python3


%prep
%setup -qn %{name}-%{commit}

%build
%if 0%{?with_python3}
%py3_build
%else
%py2_build
%endif # with_python3


%install
%if 0%{?with_python3}
%py3_install
mv %{buildroot}%{_bindir}/osbs %{buildroot}%{_bindir}/osbs-%{python3_version}
ln -s  %{_bindir}/osbs-%{python3_version} %{buildroot}%{_bindir}/osbs-3
%else
%py2_install
mv %{buildroot}%{_bindir}/osbs %{buildroot}%{_bindir}/osbs-%{python2_version}
ln -s  %{_bindir}/osbs-%{python2_version} %{buildroot}%{_bindir}/osbs-2
%endif # with_python3

ln -s  %{_bindir}/osbs-%{binaries_py_version} %{buildroot}%{_bindir}/osbs

%if 0%{?with_check}
%check
%if 0%{?with_python3}
LANG=en_US.utf8 py.test-%{python3_version} -vv tests
%else
LANG=en_US.utf8 py.test-%{python2_version} -vv tests
%endif # with_python3
%endif # with_check


%files
%doc README.md
%{_bindir}/osbs
%{!?_licensedir:%global license %doc}
%license LICENSE

%if 0%{?with_python3}
%files -n python3-osbs-client
%doc README.md
%{!?_licensedir:%global license %doc}
%license LICENSE
%{_bindir}/osbs-%{python3_version}
%{_bindir}/osbs-3
%{python3_sitelib}/osbs*
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json
%ghost %config(noreplace) %{_datadir}/osbs/orchestrator_customize.json
%ghost %config(noreplace) %{_datadir}/osbs/prod_customize.json
%ghost %config(noreplace) %{_datadir}/osbs/worker_customize.json
%else
%files -n python-osbs-client
%doc README.md
%{!?_licensedir:%global license %doc}
%license LICENSE
%{_bindir}/osbs-%{python2_version}
%{_bindir}/osbs-2
%{python2_sitelib}/osbs*
%dir %{_datadir}/osbs
%{_datadir}/osbs/*.json
%ghost %config(noreplace) %{_datadir}/osbs/orchestrator_customize.json
%ghost %config(noreplace) %{_datadir}/osbs/prod_customize.json
%ghost %config(noreplace) %{_datadir}/osbs/worker_customize.json
%endif # with_python3

%changelog
* Tue Dec 10 2019 Robert Cerven <rcerven@redhat.com> - 0.62-1
- new upstream release: 0.62

* Tue Dec 03 2019 Robert Cerven <rcerven@redhat.com> - 0.61-1
- new upstream release: 0.61

* Tue Nov 05 2019 Robert Cerven <rcerven@redhat.com> - 0.60-1
- new upstream release: 0.60

* Thu Sep 26 2019 Robert Cerven <rcerven@redhat.com> - 0.59.2-1
- new upstream release: 0.59.2

* Wed Sep 25 2019 Robert Cerven <rcerven@redhat.com> - 0.59.1-1
- new upstream release: 0.59.1

* Tue Sep 24 2019 Robert Cerven <rcerven@redhat.com> - 0.59-1
- new upstream release: 0.59

* Mon Aug 19 2019 Robert Cerven <rcerven@redhat.com> - 0.58-1
- new upstream release: 0.58

* Mon Jul 15 2019 Robert Cerven <rcerven@redhat.com> - 0.57-1
- new upstream release: 0.57

* Wed Jun 19 2019 Robert Cerven <rcerven@redhat.com> - 0.56.1-1
- new upstream release: 0.56.1

* Mon Jun 10 2019 Robert Cerven <rcerven@redhat.com> - 0.56-1
- new upstream release: 0.56

* Tue May 07 2019 Robert Cerven <rcerven@redhat.com> - 0.55-1
- new upstream release: 0.55

* Wed Mar 06 2019 Robert Cerven <rcerven@redhat.com> - 0.54-1
- new upstream release: 0.54

* Thu Jan 17 2019 Athos Ribeiro <athos@redhat.com>
- ghost customization files
- add PyYaML BRs

* Tue Jan 15 2019 Robert Cerven <rcerven@redhat.com> - 0.53.1-1
- new upstream release: 0.53.1

* Tue Jan 08 2019 Robert Cerven <rcerven@redhat.com> - 0.53-1
- new upstream release: 0.53

* Tue Dec 11 2018 Athos Ribeiro <athos@redhat.com>
- Add git-core dependency

* Tue Nov 27 2018 Athos Ribeiro <athos@redhat.com>
- remove pytest-capturelog dependency

* Fri Nov 16 2018 Athos Ribeiro <athos@redhat.com>
- drop Python 2.6 support

* Wed Nov 14 2018 Robert Cerven <rcerven@redhat.com> - 0.52-1
- new upstream release: 0.52

* Fri Oct 05 2018 Robert Cerven <rcerven@redhat.com> - 0.51-1
- new upstream release: 0.51

* Wed Aug 22 2018 Robert Cerven <rcerven@redhat.com> - 0.50-1
- new upstream release: 0.50

* Wed Jul 25 2018 Robert Cerven <rcerven@redhat.com> - 0.49-1
- new upstream release: 0.49

* Wed Jun 13 2018 Robert Cerven <rcerven@redhat.com> - 0.48-1
- new upstream release: 0.48

* Mon May 07 2018 Robert Cerven <rcerven@redhat.com> - 0.47-1
- new upstream release: 0.47

* Wed Apr 04 2018 Robert Cerven <rcerven@redhat.com> - 0.46.1-1
- new upstream release: 0.46.1

* Fri Mar 23 2018 Robert Cerven <rcerven@redhat.com> - 0.46-1
- new upstream release: 0.46

* Tue Jan 16 2018 Robert Cerven <rcerven@redhat.com> - 0.45-1
- new upstream release: 0.45

* Mon Nov 06 2017 Robert Cerven <rcerven@redhat.com> - 0.44-1
- new upstream release: 0.44

* Wed Oct 04 2017 Robert Cerven <rcerven@redhat.com> - 0.43-1
- new upstream release: 0.43

* Mon Sep 11 2017 Robert Cerven <rcerven@redhat.com> - 0.42.1-1
- new upstream release: 0.42.1

* Tue Sep 05 2017 Robert Cerven <rcerven@redhat.com> - 0.42-1
- new upstream release: 0.42

* Mon Jul 31 2017 Robert Cerven <rcerven@redhat.com> - 0.41-1
- new upstream release: 0.41

* Wed May 31 2017 Robert Cerven <rcerven@redhat.com> - 0.39-1
- new upstream release: 0.39

* Tue May 30 2017 Robert Cerven <rcerven@redhat.com> - 0.38-1
- new upstream release: 0.38

* Thu May 25 2017 Vadim Rutkovsky <vrutkovs@redhat.com> 0.37-1
- new upstream release 0.37

* Tue Apr 04 2017 Robert Cerven <rcerven@redhat.com> - 0.36-1
- new upstream release: 0.36

* Mon Mar 06 2017 Robert Cerven <rcerven@redhat.com> - 0.35-1
- new upstream release: 0.35

* Mon Feb 6 2017 Vadim Rutkovsky <vrutkovs@redhat.com> - 0.34.1-1
- new upstream release: 0.34.1

* Mon Feb 6 2017 Vadim Rutkovsky <vrutkovs@redhat.com> - 0.34
- new upstream release: 0.34

* Fri Nov 11 2016 Tim Waugh <twaugh@redhat.com> - 0.33-1
- new upstream release

* Tue Oct 11 2016 Luiz Carvalho <lucarval@redhat.com>  - 0.32-1
- new upstream release: 0.32

* Wed Sep 21 2016 Vadim Rutkovsky <vrutkovs@redhat.com> - 0.31-1
- new upstream release: 0.31

* Tue Sep 13 2016 Luiz Carvalho <lucarval@redhat.com>  - 0.30-1
- new upstream release: 0.30

* Thu Aug 18 2016 Martin Milata <mmilata@redhat.com> - 0.29-1
- new upstream release: 0.29

* Thu Jul 07 2016 Luiz Carvalho <lucarval@redhat.com> - 0.28-1
- new upstream release: 0.28

* Fri Jun 24 2016 Luiz Carvalho <lucarval@redhat.com> - 0.27-1
- new upstream release: 0.27

* Wed Jun 01 2016 Martin Milata <mmilata@redhat.com> - 0.26-1
- new upstream release: 0.26

* Tue May 31 2016 Martin Milata <mmilata@redhat.com> - 0.25-1
- new upstream release: 0.25

* Mon May 23 2016 Martin Milata <mmilata@redhat.com> - 0.24-1
- new upstream release: 0.24

* Wed May 11 2016 Martin Milata <mmilata@redhat.com> - 0.23-1
- new upstream release: 0.23

* Mon Apr 25 2016 Martin Milata <mmilata@redhat.com> - 0.22-1
- new upstream release: 0.22

* Wed Apr 20 2016 Martin Milata <mmilata@redhat.com> - 0.21-1
- new upstream release: 0.21

* Mon Apr 11 2016 Martin Milata <mmilata@redhat.com> - 0.20-1
- new upstream release: 0.20

* Thu Apr 07 2016 Martin Milata <mmilata@redhat.com> - 0.19-1
- new upstream release: 0.19

* Thu Mar 10 2016 Martin Milata <mmilata@redhat.com> - 0.18-1
- new upstream release: 0.18

* Fri Feb 12 2016 Martin Milata <mmilata@redhat.com> - 0.17-1
- new upstream release: 0.17

* Thu Jan 21 2016 Martin Milata <mmilata@redhat.com> - 0.16-1
- new upstream release: 0.16

* Fri Nov 20 2015 Jiri Popelka <jpopelka@redhat.com> - 0.15-3
- use py_build & py_install macros
- use python_provide macro
- do not use py3dir
- ship executables per packaging guidelines

* Thu Nov 05 2015 Jiri Popelka <jpopelka@redhat.com> - 0.15-2
- build for Python 3
- %%check section

* Mon Oct 19 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.15-1
- new upstream release: 0.15

* Thu Aug 06 2015 bkabrda <bkabrda@redhat.com> - 0.14-2
- renamed to osbs-client

* Wed Jul 01 2015 Martin Milata <mmilata@redhat.com> - 0.14-1
- new upstream release: 0.14

* Fri Jun 12 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.13.1-1
- new fixup upstream release: 0.13.1

* Fri Jun 12 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.13-1
- new upstream release: 0.13

* Wed Jun 10 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.12-1
- new upstream release: 0.12

* Wed Jun 03 2015 Martin Milata <mmilata@redhat.com> - 0.11-1
- new upstream release: 0.11

* Thu May 28 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.10-1
- new upstream release: 0.10

* Thu May 28 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.9-1
- new upstream release: 0.9

* Mon May 25 2015 Jiri Popelka <jpopelka@redhat.com> - 0.8-1
- new upstream release: 0.8

* Fri May 22 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.7-1
- new upstream release: 0.7

* Thu May 21 2015 Jiri Popelka <jpopelka@redhat.com> - 0.6-2
- fix %%license handling

* Thu May 21 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.6-1
- new upstream release: 0.6

* Tue May 19 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.5-1
- new upstream release: 0.5

* Tue May 12 2015 Slavek Kabrda <bkabrda@redhat.com> - 0.4-2
- Introduce python-osbs subpackage
- move /usr/bin/osbs to /usr/bin/osbs2, /usr/bin/osbs is now a symlink
- depend on python[3]-setuptools because of entrypoints usage

* Tue Apr 21 2015 Martin Milata <mmilata@redhat.com> - 0.4-1
- new upstream release

* Wed Apr 15 2015 Martin Milata <mmilata@redhat.com> - 0.3-1
- new upstream release

* Wed Apr 08 2015 Martin Milata <mmilata@redhat.com> - 0.2-2.c1216ba
- update to c1216ba

* Tue Apr 07 2015 Tomas Tomecek <ttomecek@redhat.com> - 0.2-1
- new upstream release

* Tue Mar 24 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-4
- update to 758648c8

* Thu Mar 19 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-3
- no need to require also python-requests

* Thu Mar 19 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-2
- separate executable for python 3

* Wed Mar 18 2015 Jiri Popelka <jpopelka@redhat.com> - 0.1-1
- initial spec
