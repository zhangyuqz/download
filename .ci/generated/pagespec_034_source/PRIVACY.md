# Privacy Policy

This plugin receives one PageSpec JSON string and up to 20 Dify file objects. It processes them inside the Dify plugin runtime and returns one HTML blob.

- It does not accept user-authored HTML, CSS, JavaScript, library URLs, or arbitrary network destinations.
- Browser libraries are read only from the installed, hash-checked `vendor/` directory. The renderer does not download CDN/npm assets at conversion time.
- Uploaded images are obtained through the Dify SDK file mechanism, validated by content, and embedded as local data/blob resources or replaced by a labelled placeholder.
- The plugin does not intentionally send content, images, telemetry, or analytics to third parties and does not persist input data itself.
- Generated files install a CSP that denies network connections. The final compiler-output audit rejects non-inline loadable attributes and unauthorised executable scripts.

Operators remain responsible for Dify storage, logging, retention, access control, package signatures, and deployment configuration.
