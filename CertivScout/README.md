# Certiv Scout

Downloads the latest stable macOS package, verifies Certiv's installer signature,
and imports it into Munki's `testing` catalog.

## Supplying the provisioning key

Create a local override:

```sh
autopkg make-override CertivScout.munki
```

Add these entries to the override's `Input` dictionary, replacing the
placeholder with your organization's Certiv provisioning key (starts with
`pk_`):

```xml
<key>CERTIV_PROVISIONING_KEY</key>
<string>YOUR_PROVISIONING_KEY</string>
```

The generated pkginfo contains this key in its postinstall script. Keep the
override, generated pkginfo, and AutoPkg logs private.

Set catalogs and other Munki metadata in the override. `MUNKI_REPO_SUBDIR`
defaults to the repository root. The release feed is rate limited; wait before
retrying an HTTP 429 response.

## Managed installation

The Munki recipe includes vendor-derived scripts:

- `preinstall_script` marks the installation as managed.
- `postinstall_script` waits for the daemon, registers unregistered devices,
  and launches Scout for the console user.
- `uninstall_script` invokes Scout's installed uninstaller with `--yes`.

Munki uses the package receipts for installation checks. Add organization-specific
health, registration, or profile checks in a private override's `installcheck_script`.

Deploy Certiv's configuration profiles before installation and keep them assigned
until uninstallation finishes. Configure deployment targeting in your management
system.
