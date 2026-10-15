# Certiv Scout

`CertivScout.download.recipe` downloads the latest stable macOS package from
`https://api.certiv.ai/scout/releases/latest?platform=mac` and verifies the
Developer ID Installer signature for Certiv Inc (`F556AUHXTW`). It selects the
release matching the feed's `latest` value rather than assuming release order.
The feed expects the `latest`, `version`, `filename`, and `download_url` fields
used by Certiv's current response format.

The feed requires a customer provisioning key. `CERTIV_PROVISIONING_KEY` is
empty by default: supply it privately through an override or your runner's
secret handling. Do not commit a populated key to this repository. The bearer
header is used for the release feed and explicitly cleared before the signed
package URL is downloaded. Each run queries the rate-limited feed; do not retry
HTTP 429 responses in a loop.

`CertivScout.munki.recipe` inherits the download recipe and imports the package
into the `testing` catalog. `MUNKI_REPO_SUBDIR` defaults to the repository root.
Use an override to set your catalogs, repository subdirectory, and other Munki
metadata. No repackaging is needed for the vendor's signed package.

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

Keep the populated override private. Do not add the key to the public recipe
or put it in a command that will be saved in shell history. Your runner may
instead supply this input through its secret handling.

## Managed installation

The Munki recipe includes the vendor-derived managed-install scripts:

- `preinstall_script` creates and refreshes the root-owned `.mdm-managed` marker
  before the package installs, so Scout defers its app launch to provisioning.
- `postinstall_script` waits for daemon health, skips registration when the
  daemon already has an organization, otherwise obtains a provisioning token
  and registers the device, then launches Scout for the console user.
- `uninstall_script` invokes Scout's installed uninstaller with `--yes`.

Scout 2026.10.95 changed first-time setup to create the CA certificate after
registration. The postinstall deliberately does not require that certificate
before provisioning. Its bounded daemon health check remains in place.

Deploy Certiv's configuration profiles through your management system before
installation. Profile identifiers and deployment targeting are organization
specific and are not enforced by this recipe. Keep the profiles assigned until
uninstallation finishes so the system extension can deactivate without waiting
for the user.

AutoPkg substitutes the supplied key into the generated Munki postinstall.
Keep that pkginfo and AutoPkg logs private; the public recipe contains no
customer credential. The `dT` input preserves the scripts' UTC date format
(`%Y-%m-%dT%H:%M:%SZ`) during AutoPkg variable substitution.

The public recipe uses Munki's package receipt/version detection. If your
deployment needs a custom profile-aware or registration-aware installcheck,
inherit `com.github.arubdesu.download.CertivScout` in a private recipe and run
your custom processing before `MunkiImporter`. A version-dependent script must
be merged after the download parent supplies `version`.
