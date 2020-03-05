FROM fedora:latest

RUN dnf -y update && dnf -y install git hub python3-pip && pip3 install requests gitpython

COPY *.sh /
COPY *.py /

ENTRYPOINT ["/entrypoint.sh"]
