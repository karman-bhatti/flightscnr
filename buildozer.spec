[app]

# (str) Title of your application
title = FlightScnr

# (str) Package name
package.name = flightscnr

# (str) Package domain (needed for android packaging)
package.domain = org.flightscnr

# (str) Source code directory
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,jpeg,ttf,json,txt,html,css,js

# (list) List of directory to exclude (let empty to exclude nothing)
source.exclude_dirs = tests, bin, .venv, flightscnr-venv, docs

# (str) Application versioning (method 1)
version = 1.0.0

# (list) Application requirements
# Note: We include python3 and pygame as the core UI engine.
# Flask and its dependencies run in a background thread to host the web server on the phone.
# grpcio and protobuf are required by the official FlightRadar24 Python SDK (fr24).
# If grpcio causes cross-compilation errors on your build environment, you can remove
# grpcio, protobuf, and fr24 from requirements, and FlightScnr will automatically
# run in adsb.fi-only mode.
requirements = python3==3.10.12,hostpython3==3.10.12,pygame,requests,urllib3,certifi,idna,charset-normalizer,qrcode,pillow,websockets,python-dotenv,flask,jinja2,click,werkzeug,itsdangerous,blinker,xyzservices,branca,folium

# (str) Supported orientations (one of portrait, landscape, sensorPortrait, sensorLandscape or all)
orientation = all

# (bool) Use fullscreen or not
fullscreen = 1

# (list) Permissions
android.permissions = INTERNET, ACCESS_FINE_LOCATION, ACCESS_COARSE_LOCATION

# (int) Target Android API, should be as high as possible.
android.api = 33

# (int) Minimum API your APK will support.
android.minapi = 21

# (str) Android NDK version to use
android.ndk = 25b

# (list) Architectures to build for (e.g. arm64-v8a for modern 64-bit phones)
android.archs = arm64-v8a, armeabi-v7a

# (bool) Allow Google Play backups
android.allow_backup = True

# (str) Format used to package the app (apk or aab)
android.release_artifact = apk

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with compiler output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 1
