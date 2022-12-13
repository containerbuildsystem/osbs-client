%global binaries_py_version %{python3_version}

%if 0%{?fedora}
# rhel/epel has older incompatible version of pytest (no caplog)
%global with_check 1
%endif

%global osbs_obsolete_vr 0.14-2

Name:           osbs-client
Version:        2.2.0
Release:        1%{?dist}

Summary:        Python command line client for OpenShift Build Service
Group:          Development/Tools
License:        BSD
URL:            https://github.com/containerbuildsystem/osbs-client
Source0:        https://github.com/containerbuildsystem/osbs-client/archive/%{version}.tar.gz

BuildArch:      noarch

Requires:       python3-osbs-client = %{version}-%{release}

BuildRequires:  git-core
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools

%if 0%{?with_check}
BuildRequires:  python3-dateutil
BuildRequires:  python3-pytest
BuildRequires:  python3-flexmock
BuildRequires:  python3-six
BuildRequires:  python3-dockerfile-parse
BuildRequires:  python3-requests
BuildRequires:  python3-requests-kerberos
BuildRequires:  python3-PyYAML
BuildRequires:  python3-jsonschema
%endif # with_check

Provides:       osbs = %{version}-%{release}
Obsoletes:      osbs < %{osbs_obsolete_vr}

%description
It is able to query OpenShift v3 for various stuff related to building images.
It can initiate builds, list builds, get info about builds, get build logs...
This package contains osbs command line client.

%package -n python3-osbs-client
Summary:        Python 3 module for OpenShift Build Service
Group:          Development/Tools
License:        BSD
Requires:       python3-dockerfile-parse
Requires:       python3-jsonschema
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


%prep
%setup -q

%build
%py3_build


%install
%py3_install
mv %{buildroot}%{_bindir}/osbs %{buildroot}%{_bindir}/osbs-%{python3_version}
ln -s  %{_bindir}/osbs-%{python3_version} %{buildroot}%{_bindir}/osbs-3
ln -s  %{_bindir}/osbs-%{binaries_py_version} %{buildroot}%{_bindir}/osbs

%files
%doc README.md
%{_bindir}/osbs
%{!?_licensedir:%global license %doc}
%license LICENSE

%files -n python3-osbs-client
%doc README.md
%{!?_licensedir:%global license %doc}
%license LICENSE
%{_bindir}/osbs-%{python3_version}
%{_bindir}/osbs-3
%{python3_sitelib}/osbs*


%changelog
* Tue Dec 13 2022 mkosiarc <mkosiarc@redhat.com> 2.2.0-1
- new upstream release: 2.2.0

* Thu Nov 03 2022 Robert Cerven <rcerven@redhat.com> 2.1.0-1
- new upstream release: 2.1.0

* Thu Oct 06 2022 rcerven <rcerven@redhat.com> 2.0.0-1
- new upstream release: 2.0.0

* Fri Jul 30 2021 mkosiarc <mkosiarc@redhat.com> 1.11.0-1
- new upstream release: 1.11.0

* Wed Jun 09 2021 Robert Cerven <rcerven@redhat.com> 1.10.0-1
- new upstream release: 1.10.0

* Fri Apr 16 2021 Robert Cerven <rcerven@redhat.com> 1.9.0-1
- new upstream release: 1.9.0

* Mon Mar 15 2021 Martin Bašti <mbasti@redhat.com> 1.8.0-1
- new upstream release: 1.8.0

* Wed Feb 03 2021 Chenxiong Qi <cqi@redhat.com> 1.7.0-1
- new upstream release: 1.7.0

* Fri Jan 29 2021 Chenxiong Qi <qcxhome@gmail.com> 1.6.0-1
- new upstream release: 1.6.0

* Mon Jan 18 2021 Martin Bašti <mbasti@redhat.com> 1.5.0-1
- new upstream release: 1.5.0

* Fri Nov 06 2020 Robert Cerven <rcerven@redhat.com> 1.4.0-1
- new upstream release: 1.4.0

* Thu Sep 17 2020 Martin Bašti <mbasti@redhat.com> 1.3.0-1
- new upstream release: 1.3.0

* Thu Aug 27 2020 Robert Cerven <rcerven@redhat.com> 1.2.0-1
- new upstream release: 1.2.0

* Wed Jul 29 2020 Robert Cerven <rcerven@redhat.com> 1.1.0-1
- new upstream release: 1.1.0

* Fri Jul 03 2020 Martin Bašti <mbasti@redhat.com> 1.0.0-1
- new upstream release: 1.0.0

* Tue Jun 02 2020 Robert Cerven <rcerven@redhat.com> 0.67-1
- new upstream release: 0.67

* Fri Apr 24 2020 Martin Bašti <mbasti@redhat.com> 0.66-1
- new upstream release: 0.66

* Wed Apr 01 2020 Martin Bašti <mbasti@redhat.com> 0.65-1
- new upstream release: 0.65

* Wed Mar 04 2020 Robert Cerven <rcerven@redhat.com> - 0.64.1-1
- new upstream release: 0.64.1

* Tue Feb 18 2020 Robert Cerven <rcerven@redhat.com> - 0.64-1
- new upstream release: 0.64

* Tue Jan 21 2020 Robert Cerven <rcerven@redhat.com> - 0.63-1
- new upstream release: 0.63

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
