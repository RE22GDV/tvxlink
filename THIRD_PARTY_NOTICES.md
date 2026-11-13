# Third-party notices

TVXLink is MIT-licensed. It is an independent project and is **not affiliated with, endorsed by
or supported by PROMAX Electronica S.L.** "PROMAX" and "TV EXPLORER" are trademarks of their owners
and are used only to name the hardware this program talks to.

| Component | How it is used | License / terms |
|---|---|---|
| PROMAX NetUpdate 4.35 (TI TUSB3410 VCP driver, `umpf3410.i51` firmware) | **not included**; downloaded from promax.es on the first start and unpacked locally, SHA-256 pinned | PROMAX / Texas Instruments terms (`LICENSE.rtf` inside the package) |
| 7-Zip 24.09 | **not included**; downloaded from the official release only if no 7-Zip is installed, to unpack the NetUpdate installer | GNU LGPL 2.1+ with unRAR restriction |
| Python embeddable 3.12 | **not included**; downloaded on Windows when no Python is installed | PSF License |
| pyserial, pyusb, libusb-package (libusb 1.0) | installed from PyPI | BSD-3-Clause, BSD-3-Clause, LGPL-2.1+ |
| KingOfSat transponder and channel data | facts (frequencies, symbol rates, channel names) parsed into `tvxlink/satellites/*.json` with the build date; the source is credited in the window | © kingofsat.net — please respect their terms when updating often |
| ETSI EN 302 307-1 / EN 300 421 | QEF Es/N0 and Eb/N0 thresholds (`tvxlink/satdb.py`) | figures cited from the standards |

Protocol knowledge was obtained by studying PROMAX NetUpdate 3/4 and RM-404 for interoperability.
No PROMAX code or binaries are distributed; `reverse/` holds only our own notes and scripts.
