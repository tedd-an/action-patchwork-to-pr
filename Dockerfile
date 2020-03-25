FROM fedora:latest

RUN dnf -y update && dnf -y install git python3-pip && pip3 install requests gitpython pygithub

COPY *.sh /
COPY *.py /

ENTRYPOINT ["/entrypoint.sh"]
