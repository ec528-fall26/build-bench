Name:           buildbench-hello
Version:        1.0
Release:        1
Summary:        Minimal package for the Build-Bench Starter Kit
License:        MIT
BuildArch:      noarch

%description
A minimal package used by the trusted Build-Bench one-command demo.

%prep

%build
# BUILD-BENCH-DEMO-BROKEN
echo "intentional Build-Bench demo failure" >&2
exit 1

%install
mkdir -p %{buildroot}%{_datadir}/buildbench-hello
echo "Hello from Build-Bench" > %{buildroot}%{_datadir}/buildbench-hello/hello.txt

%files
%dir %{_datadir}/buildbench-hello
%{_datadir}/buildbench-hello/hello.txt
