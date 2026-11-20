FROM python:3.12-alpine
WORKDIR /proxy
COPY provider_egress_proxy.py egress_policy.json /proxy/
EXPOSE 3128
HEALTHCHECK --interval=2s --timeout=2s --retries=30 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 3128), 1).close()"]
ENTRYPOINT ["python", "/proxy/provider_egress_proxy.py", "/proxy/egress_policy.json"]
