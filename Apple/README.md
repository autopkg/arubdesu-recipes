# Packages from Apple's software update catalog

These recipes find packages in Apple's public software update catalog: the macOS installer (`InstallAssistant.pkg`), and the Command Line Tools component package (`CLTools_Executables.pkg`). From the latter, `AppleGit` packages Apple's own Git, with its keychain credential helper, so a Mac gets Git without the rest of the Command Line Tools.

| Recipe | Identifier | What it does |
| --- | --- | --- |
| `MacOSInstallAssistant.download` | `com.github.autopkg.arubdesu-recipes.macosinstallassistant.download` | downloads the highest-versioned macOS installer package and verifies its signature |
| `MacOSInstallAssistant.munki` | `com.github.autopkg.arubdesu-recipes.munki.macosinstallassistant` | imports it into Munki |
| `MacOSInstallAssistant.install` | `com.github.autopkg.arubdesu-recipes.install.macosinstallassistant` | installs it on the build Mac |
| `AppleCLTools.download` | `com.github.autopkg.arubdesu-recipes.applecltools.download` | downloads every component package of the highest-versioned Command Line Tools product, and its .dist |
| `AppleCLTools.pkg` | `com.github.autopkg.arubdesu-recipes.applecltools.pkg` | verifies each component and rebuilds the product from Apple's .dist |
| `AppleCLTools.munki` | `com.github.autopkg.arubdesu-recipes.munki.applecltools` | imports the product into Munki with the .dist's macOS range and architectures |
| `AppleCLTools.install` | `com.github.autopkg.arubdesu-recipes.install.applecltools` | installs it on the build Mac |
| `AppleGit.pkg` | `com.github.autopkg.arubdesu-recipes.applegit.pkg` | builds a package of Apple's Git from the Command Line Tools, at `/opt/apple-git` |
| `AppleGit.munki` | `com.github.autopkg.arubdesu-recipes.munki.applegit` | imports it into Munki |
| `AppleGit.install` | `com.github.autopkg.arubdesu-recipes.install.applegit` | installs it on the build Mac |

## The catalog

Both download recipes take `CATALOG_URL`, which defaults to the merged catalog for macOS 27:

```text
https://swscan.apple.com/content/catalogs/others/index-27-26-15-14-13-12-10.16-10.15-10.14-10.13-10.12-10.11-10.10-10.9-mountainlion-lion-snowleopard-leopard.merged-1.sucatalog.gz
```

On 2026-10-22 it listed 21 macOS installers and 12 Command Line Tools products, the newest being Command Line Tools for Xcode 27.0 (product `082-83364`, posted 2026-09-09). The macOS 15 catalog listed the same 21 installers, but its newest Command Line Tools product was posted on 2025-05-28.

A `URLDownloader` step fetches the catalog, so its ETag caching shows in the run like any other download, and `AppleSoftwareUpdateCatalogProvider` reads the saved file.

## `AppleSoftwareUpdateCatalogProvider`

The processor is ported from [`legacy-files/process_uma.py`](https://github.com/macadmins/sofa/blob/12305e6249dac1c4a26554127f8c1ed440a06a20/legacy-files/process_uma.py#L1-L5) in macadmins/sofa (commit `12305e6`, 2025-09-20), which finds macOS installers for the SOFA feed. That code is by Alex Ferrer Alequin (Arequ), with error handling by Allister Banks (arubdesu), and is Apache-2.0 like this repo; the port is credited in the processor's header. It generalizes the original from `InstallAssistant.pkg` to any package and fixes two bugs, which are also worth sending back to sofa:

- [`sort_installers`](https://github.com/macadmins/sofa/blob/12305e6249dac1c4a26554127f8c1ed440a06a20/legacy-files/process_uma.py#L129-L133) sorts version strings as text, so `26.9` sorts above `26.10`. The port compares with AutoPkg's `APLooseVersion`.
- [`get_metadata`](https://github.com/macadmins/sofa/blob/12305e6249dac1c4a26554127f8c1ed440a06a20/legacy-files/process_uma.py#L91-L102) prints a failed `.dist` download and then parses `dist_data`, which was never assigned on that path, so the next line raises `UnboundLocalError`. The port raises `ProcessorError` with the URL.

Control flow:

1. Read `catalog_path`, gzip-compressed or plain.
2. Select every product with a package whose URL file name is `package_filename`.
3. Fetch each product's English `.dist` with curl `--fail`. A failed fetch stops the run.
4. Read the title from `<title>`. A key such as the Command Line Tools' `SU_TITLE` is resolved through the `.dist`'s `<strings language="English">` table.
5. Read the version and build from the `<auxinfo>` dictionary's `VERSION` and `BUILD` keys, which installer `.dist` files carry. With `pkg_ref_id` set, read the version from that `pkg-ref` instead, matching either its `id` or its `packageIdentifier`, because older Command Line Tools `.dist` files use the short id `CLTools_Executables`. A product whose `.dist` names no version is skipped with a message.
6. Keep products whose version starts with `major_version`, when set, and whose title contains `title_contains`, when set.
7. Choose the highest `APLooseVersion`, with the newer post date breaking a tie. Post date alone is wrong: on 2026-10-22 the newest-posted installer, posted after macOS 27.0.1, was macOS Sequoia 15.8.1.

| Input | Meaning |
| --- | --- |
| `catalog_path` | the downloaded catalog |
| `package_filename` | `InstallAssistant.pkg` or `CLTools_Executables.pkg` |
| `major_version` | first version component to keep, e.g. `26`; empty keeps all |
| `title_contains` | text the resolved title must contain |
| `pkg_ref_id` | take the version from this `pkg-ref` instead of `<auxinfo>` |

Outputs: `url`, `version`, `build` (empty when `pkg_ref_id` supplied the version), `title`, `product_key`, `post_date`, `package_size`, `dist_url`, `minimum_os_version` (the `min` of the .dist's `<allowed-os-versions>`), `maximum_os_version` (its exclusive `before` turned into an inclusive bound for Munki: 28.0 becomes 27.99, 15.4 becomes 15.3.99), `host_architectures` (the .dist's `hostArchitectures`, comma separated; Command Line Tools .dist files carry it, installer .dist files do not), `product_packages` (every package in the product, with its URL, size and digest), and `additional_pkginfo` (those bounds and architectures as Munki pkginfo keys, for `MunkiPkginfoMerger`).

On the 2026-10-22 catalog it chose Command Line Tools for Xcode 27.0, version `27.0.0.0.1788430756`, for macOS 26.6 or later on `arm64`; macOS 27 Golden Gate 27.0.1 (`26A434`) with no `major_version`; and macOS Tahoe 26.7.1 (`25G241`), installable from macOS 10.15, with `major_version` set to `26`.

## `MacOSInstallAssistant`

`MacOSInstallAssistant.download`:

1. `URLDownloader` saves the catalog as `software-update-catalog.sucatalog.gz`.
2. `AppleSoftwareUpdateCatalogProvider` finds `InstallAssistant.pkg`, limited to `MAJOR_VERSION` when it is set.
3. `URLDownloader` saves `InstallAssistant-<version>-<build>.pkg`, 15-18 GB.
4. `EndOfCheckPhase`.
5. `CodeSignatureVerifier` checks the package against Apple's Software Update chain: Software Update, Apple Software Update Certification Authority, Apple Root CA. Confirm that chain on the first real download; no installer package has been downloaded with this recipe yet.

Installing the package puts `/Applications/Install macOS <name>.app` in place. It does not upgrade macOS; the installer application, or its `startosinstall`, does that. `MacOSInstallAssistant.munki` imports the package with `version` set to the macOS version and `minimum_os_version` set to the .dist's own minimum, says so in its description, and sets `unattended_install` to false.

Give each macOS line its own Munki override, setting both `MAJOR_VERSION` (for example `26`) and `NAME` (for example `MacOSInstallAssistant26`). Munki treats items with the same name as versions of one thing, so one shared name would replace the Tahoe installer with the macOS 27 installer.

`MacOSInstallAssistant.install` runs `Installer` on `%pathname%`.

## `AppleCLTools`

These recipes install what Software Update installs for the Command Line Tools: every component package of the product, under Apple's own .dist. They replace a Munki `nopkg` item whose script sets `/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress` and runs `softwareupdate -i` on the last Command Line Tools label `softwareupdate -l` lists.

The product in the catalog is nine component packages for 27.0, and the .dist puts all of them in its one choice, `su`, so they install together:

| Component | Size | Contents |
| --- | --- | --- |
| `CLTools_Executables.pkg` | 350.5 MB | the tools: clang, git, make and the rest |
| `CLTools_macOSNMOS_SDK.pkg`, `CLTools_macOSLMOS_SDK.pkg` | 70.6 MB, 61.6 MB | the macOS SDKs |
| `CLTools_SwiftBackDeploy.pkg` | 49.1 MB | Swift back-deployment libraries |
| `CLTools_macOS_SDK.pkg` and four `CLTools_macOS_DevSDK_Remove_*.pkg` | under 0.1 MB each | the `MacOSX.sdk` links and removal of older SDKs |

`AppleCLTools.download`:

1. `URLDownloader` saves the catalog.
2. `AppleSoftwareUpdateCatalogProvider` finds the highest-versioned product carrying `CLTools_Executables.pkg`, with `pkg_ref_id` set to `com.apple.pkg.CLTools_Executables`, `title_contains` set to `Command Line Tools`, and `major_version` set to `MAJOR_VERSION` when it is not empty.
3. `AppleSoftwareUpdateProductDownloader` downloads every package the product lists, and its English .dist as `product.dist`, into `downloads/products/<product key>/`. Each package's size must match the catalog's `Size`, and the SHA-1 of its compressed xar table of contents must match `Digest`; that table of contents holds the checksum of every file in the package and is what Apple's signature covers. (`Digest` is not the SHA-1 of the whole file; the 27.0 `CLTools_Executables.pkg` confirmed this.) A package already present with the right size and digest is not downloaded again. Files the product no longer lists, and other products' folders, are removed. It sets `pathname` to `CLTools_Executables.pkg`, which `AppleGit` uses.
4. `EndOfCheckPhase`.

`AppleCLTools.pkg` runs `AppleSoftwareUpdateProductBuilder`:

1. Every package the .dist's `pkg-ref` elements name must be present.
2. Each one's installer signature is checked by core `CodeSignatureVerifier`'s own package check against `Software Update`, `Apple Software Update Certification Authority`, `Apple Root CA`. Confirm that chain on the first real run, since these are the component packages from the catalog.
3. `productbuild --distribution product.dist --package-path <product folder>` writes `AppleCLTools-<version>.pkg`. The result keeps Apple's architecture check, its macOS range and its choice; the product itself is unsigned. An existing package of the same name is kept.

A test with placeholder components carrying the 27.0 .dist's package names and identifiers built a product containing all nine, and `productbuild` reported "Supported OS versions: [Min: 26.6, Before: 28.0]". The `su` choice is visible only during an upgrade or while the install-on-demand flag file exists, but `installer -showChoicesXML` on the built product reported it hidden and still selected and enabled on this Mac, which has neither. So installing the product needs no flag file.

`AppleCLTools.munki` runs `MunkiPkginfoMerger` with the provider's `additional_pkginfo`, then `MunkiImporter` on `%pkg_path%`. The pkginfo gets `minimum_os_version`, `maximum_os_version` and `supported_architectures` from the .dist, and receipts for all nine components, which are the receipts a Software Update install leaves. `AppleCLTools.install` runs `Installer` on `%pkg_path%`.

To match `softwareupdate` across a fleet, make one override per Command Line Tools line, each with its own `MAJOR_VERSION` and the same `NAME`; Munki installs the highest version whose macOS range and architectures fit each Mac. On the 2026-10-22 catalog:

| `MAJOR_VERSION` | Version | macOS | Architectures | Packages |
| --- | --- | --- | --- | --- |
| empty or `27` | 27.0.0.0.1788430756 | 26.6 to 27.99 | `arm64` | 9 |
| `16` | 16.4.0.0.1.1747106510 | 15.3 to 15.99 | `x86_64`, `arm64` | 9 |
| `15` | 15.3.0.0.1.1708646388 | 14.0 to 15.3.99 | `x86_64`, `arm64` | 5 |

Neither the macOS 27 nor the macOS 26 catalog has a line for macOS 26.0 to 26.5, so `softwareupdate` finds no Command Line Tools there either. Setting `NAME` to an existing item's name, such as `XcodeCLITools`, makes these pkginfos newer versions of it; a `nopkg` version left in the catalog stays the item on any macOS no line covers.

## `AppleGit`

Apple's Git, as the Command Line Tools ship it, runs without the rest of them. Checked with the Command Line Tools for Xcode 27.0 (`27.0.0.0.1788430756`, Git 2.54.0, Apple Git-157):

- Its 10 Mach-O files (`git`, `git-shell`, and eight helpers in `libexec/git-core`, `git-credential-osxkeychain` among them) are signed by Apple with `com.apple.*` identifiers and link only `/usr/lib` and `/System`. They are `arm64` only, with a minimum of macOS 14.0.
- It is built to find its helpers, its `share/git-core/gitconfig` and its templates relative to its own location. A copy moved to another folder reported that folder's `libexec/git-core` as its exec path, read that folder's gitconfig, and ran `git ls-remote` over HTTPS.
- Its `share/git-core/gitconfig` sets `credential.helper = osxkeychain` and `init.defaultBranch = main`, so the keychain helper works with no other configuration. Credential helpers serve HTTPS remotes and `git send-email`'s SMTP login, which [calls `Git::credential`](https://github.com/git/git/blob/b0b304c10a65882a8e36abbb694603648a78edfd/git-send-email.perl#L1482-L1489); SSH remotes use macOS's `/usr/bin/ssh`, which keeps keys in the keychain with `UseKeychain` and `ssh-add --apple-use-keychain`.

What it includes:

- `git send-email` is its only Perl command. It runs on macOS's own `/usr/bin/perl` 5.34.1 with Git's modules in `share/git-core/perl`; macOS's Perl has every module it uses except the optional `Email::Valid`.
- `git request-pull`, `git subtree`, `git submodule` and `git mergetool` are `/bin/sh` scripts.
- There is no `git svn`, no CVS commands, no `git archimport` and no `git instaweb`.
- `git p4` is a Python script for `/usr/bin/python3`, a shim that asks to install the Command Line Tools; it is not usable without them.

`AppleGit.pkg`, with `AppleCLTools.download` as its parent:

1. `FlatPkgUnpacker` expands the component package into `unpack/`, and `PkgPayloadUnpacker` extracts its payload into `payload/`. `PkgPayloadUnpacker` tries `ditto` and then `aa`; confirm on the first real run that one of them reads this payload.
2. `CodeSignatureVerifier`, three times, checks `bin/git`, `libexec/git-core/git-remote-http` and `libexec/git-core/git-credential-osxkeychain` in the payload against `identifier "com.apple.<name>" and anchor apple`, before anything is copied into the pkgroot. The package itself passed Apple's Software Update signature check in `AppleCLTools.download`.
3. `PkgRootCreator` creates `opt`, `opt/apple-git`, `private`, `private/etc`, `private/etc/manpaths.d`, `private/etc/paths.d` and `usr` at 0755, and `usr/local` and `usr/local/bin` at 0775.
4. `Rsync` copies, from `payload/Library/Developer/CommandLineTools/usr/` into `opt/apple-git/`, exactly: `bin/git` and the four `bin/git-*` commands, all of `libexec/git-core` and `share/git-core`, and the `git*` pages in `share/man/man*/`. It runs `/usr/bin/rsync -a -m` with include rules for those paths and `--exclude=*` for everything else, so symlinks such as `libexec/git-core/git -> ../../bin/git`, modes and times are kept, and folders left empty are dropped.
5. `FileCreator` writes `/private/etc/paths.d/apple-git`, holding `/opt/apple-git/bin`, and `/private/etc/manpaths.d/apple-git`, holding `/opt/apple-git/share/man`.

   The `paths.d` entry is the one that makes `man git` work. With `MANPATH` unset, the default, `path_helper` ignores `/etc/manpaths.d` (its man page: "The MANPATH environment variable will not be modified unless it is already set"), and `man` builds its search path from `PATH`, taking `share/man` beside each `bin` folder. `path_helper` appends `/opt/apple-git/bin` after `/usr/bin`, where Apple's shims already shadow all five commands in it, so `PATH` lookups don't change. The `manpaths.d` entry covers people who set `MANPATH`. Seen on a test VM on 2026-10-23: `man -w git` failed in a new login shell with only the `manpaths.d` entry.
6. `FileCreator`, five times, writes a two-line wrapper at mode 0755 for each of `/usr/local/bin/git`, `git-receive-pack`, `git-shell`, `git-upload-archive` and `git-upload-pack`: `#!/bin/sh` then `exec /opt/apple-git/bin/<name> "$@"`. `/usr/bin` holds Apple's shims for exactly those five, which ask to install the Command Line Tools, and `/usr/local/bin` comes first in the default `PATH`.

   These are wrappers rather than symlinks because of how Git finds its own files. On macOS, Git takes its own path from `_NSGetExecutablePath`, which returns the path it was started by without resolving symlinks (`exec-cmd.c` in Git's source; on Linux it resolves the path instead). Started through a `/usr/local/bin/git` symlink, Git treats `/usr/local` as its prefix and looks there for its helpers, templates and system gitconfig. HTTPS clones then fail with `warning: templates not found in /usr/local/share/git-core/templates` and `git: 'remote-https' is not a git command`, and the keychain credential helper setting is never read. Seen on a test VM on 2026-10-23, and reproduced against the built pkgroot. `exec` starts the real file in `/opt/apple-git/bin`, so Git finds everything beside it.
7. `PkgCreator` builds `AppleGit-<CLT_ARCH>-<version>.pkg`, identifier `com.github.arubdesu.apple-git`, version the Command Line Tools package version, with `opt` and `private` root:wheel and `usr` root:admin, keeping modes. There are no scripts.

A dry run from the installed Command Line Tools 27.0 measured 28.9 MB of files and a 12.9 MB package. No file is modified, so the package supports exactly the architectures Apple built; `CLT_ARCH` and `MINIMUM_OS_VERSION` are set by hand from `lipo -archs` and the binary's `LC_BUILD_VERSION`.

`AppleGit.munki` runs `MunkiImporter`; the pkginfo sets `supported_architectures` to `CLT_ARCH` and `minimum_os_version` to `MINIMUM_OS_VERSION`. `AppleGit.install` runs `Installer` on `%pkg_path%`.

HTTPS with Apple's Git goes through `/usr/lib/libcurl.4.dylib`, whose default backend is LibreSSL reading `/etc/ssl/cert.pem`, a bundle file macOS ships, not the keychain trust store.

### Open question: redistribution

Whether Apple's license allows deploying Apple's Git across a fleet without the rest of the Command Line Tools is unresolved. The license in the Command Line Tools 27.0 `.dist` allows "only as many internal use copies of the Developer Software as reasonably necessary to use the Developer Software as permitted under this License" and distribution of "such copies only to your employees whose job duties require them to so use the Developer Software". Settle that before deploying `AppleGit` beyond testing.

## Checks

```sh
pkgutil --pkg-info com.github.arubdesu.apple-git
command -v git && git --version && git --exec-path
git config --show-origin credential.helper
man -w git
```

`git --exec-path` should print `/opt/apple-git/libexec/git-core`, and `git config --show-origin` should name `/opt/apple-git/share/git-core/gitconfig`. `man -w git` needs a new login shell, because `path_helper` reads `/etc/paths.d` at login.

## Failure map

Every processor stops a run with `ProcessorError`; run standalone, `execute_shell` exits 10. The rows follow the control flow.

| Step | Condition |
| --- | --- |
| `URLDownloader` (catalog) | the catalog does not download |
| `AppleSoftwareUpdateCatalogProvider` | the catalog is not a readable plist, gzip-compressed or plain |
| | a `.dist` fails to download or is not XML |
| | no product carries `package_filename` with a version, `major_version` and `title_contains` that match |
| `URLDownloader` (package), `AppleSoftwareUpdateProductDownloader` | a package or the .dist does not download, or a package's size or table-of-contents digest differs from the catalog |
| `CodeSignatureVerifier` (package) | the package's signing chain is not Software Update, Apple Software Update Certification Authority, Apple Root CA |
| `FlatPkgUnpacker`, `PkgPayloadUnpacker` | the package or its payload does not expand |
| `CodeSignatureVerifier` (`git`, `git-remote-http`, keychain helper) | an extracted binary is missing or fails Apple's requirement |
| `Rsync` | rsync exits nonzero, for example because the payload layout changed |
| `FileCreator` | a `/usr/local/bin` wrapper, the paths entry or the manpaths entry cannot be written |
| `AppleSoftwareUpdateProductBuilder` | the .dist cannot be read or names a package that is missing, a component fails its signature check, or `productbuild` fails |
| `PkgCreator` | autopkgserver rejects the request |

A product whose `.dist` names no version only logs a message.
