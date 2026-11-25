# Copia reducida de docker/Dockerfile del repo privado (sin CA/proxy).
FROM odoo:20.0@sha256:6709a38456a588273462c83400a0b6dbe09038deefc05846a8d3d25231dbb82e

USER root

# Dependencias de l10n_cl_fe + pruebas (cryptography, lxml; websocket-client para browser_js)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --break-system-packages \
        "facturacion_electronica>=0.25.0" num2words xlsxwriter pillow \
        "PyMuPDF>=1.21.1" "pytesseract>=0.3.10" lxml cryptography \
        websocket-client

# Chrome headless para las pruebas HttpCase/browser_js de Odoo (busca `google-chrome`
# en el PATH). Ubuntu 24.04 solo trae chromium como snap: se usa Chrome for Testing
# (chrome-headless-shell, versión fija) y sus bibliotecas desde apt.
ARG CHROME_VERSION=158.0.8127.36
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
        libpango-1.0-0 libcairo2 libasound2t64 libxshmfence1 libdbus-1-3 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -c "import os, ssl, urllib.request; \
ctx = ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt'); \
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx)); \
url = 'https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-headless-shell-linux64.zip'; \
open('/tmp/chrome.zip', 'wb').write(opener.open(url).read())" \
    && python3 -m zipfile -e /tmp/chrome.zip /opt/ \
    && rm /tmp/chrome.zip \
    && chmod +x /opt/chrome-headless-shell-linux64/chrome-headless-shell \
    && ln -s /opt/chrome-headless-shell-linux64/chrome-headless-shell /usr/local/bin/google-chrome \
    && google-chrome --version

# Privacidad: partner_autocomplete se auto-instala con iap_mail y envía a la IAP de Odoo
# (partner-autocomplete.odoo.com) el nombre y el RUT/VAT de los contactos al escribirlos, y los
# datos de la compañía al iniciar sesión. Se quita de la imagen para que no se instale (ni se
# auto-instale) en ninguna base. No se puede impedir desde l10n_cl_fe: un 'excludes' hace fallar
# `-i l10n_cl_fe` y desinstalarlo en el post_init recarga el registro y salta las pruebas; un
# módulo con el mismo nombre en /mnt/addons no lo reemplaza (odoo/addons va primero).
# Para habilitarlo (opt-in explícito del cliente), borrar estas líneas.
# (La imagen trae además una copia parcial en dist-packages/addons que Odoo también encuentra.)
RUN for d in /usr/lib/python3/dist-packages/odoo/addons /usr/lib/python3/dist-packages/addons; do \
        rm -rf "$d/partner_autocomplete" "$d/pos_partner_autocomplete"; \
    done

USER odoo
