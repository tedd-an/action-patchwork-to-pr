FROM blueztestbot/bluez-build:latest

COPY *.sh /
COPY *.py /

ENTRYPOINT ["/entrypoint.sh"]
