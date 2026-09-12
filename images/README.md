# Logo

The site uses `logo.svg` by default — a stylized vector approximation of the Arrowhead Paesano YouTube avatar.

## Use your real logo PNG

Save your channel avatar PNG (the cartoon character with the raised fist) here as:

```
public/images/logo.png
```

The header and hero will automatically prefer `logo.png` over `logo.svg` when it's present (a small `<picture>` fallback handles the swap with no code changes). Recommended dimensions: **400×400** square, transparent or red background.

After dropping the file in, hard-refresh the browser (Ctrl+F5) to bust the cache.
