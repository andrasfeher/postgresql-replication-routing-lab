FROM gcr.io/etcd-development/etcd:v3.6.0 AS tools
FROM debian:trixie-slim
COPY --from=tools /usr/local/bin/etcdctl /usr/local/bin/etcdctl
COPY bootstrap-auth.sh admin.sh /usr/local/bin/
ENTRYPOINT ["/usr/local/bin/admin.sh"]
