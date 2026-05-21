# Web UI structure

This package contains the browser frontends served by the FastAPI backend when Web UI hosting is enabled.

```text
web/
├── index.html          # desktop /web shell
├── assets/             # desktop /web static assets mounted at /web/assets
│   ├── css/app.css
│   └── js/app.js
└── m/                  # upstream mobile /m app
    ├── index.html
    ├── css/app.css
    └── js/
```

The desktop UI is served at `/web` and loads its assets from `/web/assets/...`. The mobile UI remains available at `/m/`.
