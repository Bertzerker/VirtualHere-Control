#!/bin/sh
set -eu

CLIENT_NAME="${1:-windows-pc}"
CLIENT_IP="${2:-192.168.8.125}"
CLIENT_DNS="${3:-windows-pc.local}"

mkdir -p certs
cd certs

if [ ! -f ca.key ] || [ ! -f ca.pem ]; then
  openssl genrsa -out ca.key 2048
  openssl req -new -x509 -days 3650 -key ca.key -out ca.pem -subj "/CN=VH Control CA"
fi

cat > "${CLIENT_NAME}.ext" <<EOF
subjectAltName = DNS:${CLIENT_NAME}, DNS:${CLIENT_DNS}, IP:${CLIENT_IP}
EOF

openssl genrsa -out "${CLIENT_NAME}.key" 2048
openssl req -new -key "${CLIENT_NAME}.key" -out "${CLIENT_NAME}.csr" -subj "/CN=${CLIENT_NAME}"
openssl x509 -req -days 3650 \
  -in "${CLIENT_NAME}.csr" \
  -CA ca.pem \
  -CAkey ca.key \
  -CAcreateserial \
  -out "${CLIENT_NAME}.crt" \
  -extfile "${CLIENT_NAME}.ext"

openssl x509 -in "${CLIENT_NAME}.crt" -noout -subject -issuer -ext subjectAltName

echo "Created:"
echo "  certs/ca.pem"
echo "  certs/${CLIENT_NAME}.crt"
echo "  certs/${CLIENT_NAME}.key"
