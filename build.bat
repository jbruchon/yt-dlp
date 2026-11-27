if "%PY%" EQU "" set PY=py
%PY% --version 2>NUL || set PY=python3
%PY% --version || exit 1

%PY% -m pip install -U --pre "yt-dlp[default]"
%PY% -m pip install -U --pre pyinstaller
%PY% devscripts/install_deps.py --include pyinstaller
%PY% devscripts/make_lazy_extractors.py
%PY% -m bundle.pyinstaller
