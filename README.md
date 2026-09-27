# SSH & Codespaces health keepalive experiment

Eksperimen koneksi berkala ke server SSH dan GitHub Codespaces, dengan laporan
uptime, CPU, memori, serta 5 proses dengan penggunaan CPU tertinggi.
Workflow bisa dipicu manual (`workflow_dispatch`) dan setiap jam pada menit 34
(`34 * * * *`, UTC; di WIB juga menit 34 setiap jam).

## Konfigurasi

1. Gunakan `servers.yaml.example` sebagai template (salin ke `servers.yaml` jika perlu).
   Edit `servers.yaml`, ganti host/nama Codespace dan set `enabled: true` untuk target yang dipakai.
2. Tambahkan kredensial di repository **Settings → Secrets and variables → Actions**.
   Nama setelah `secret:` harus persis sama dengan nama repository secret.
3. Untuk SSH, isi `privatekey` atau `password`; keduanya boleh diisi untuk mencoba key
   lalu autentikasi password. Private key harus tanpa passphrase. Login tidak wajib root.
4. Isi `known_hosts` dengan referensi secret/env berisi entri OpenSSH milik server.
   Verifikasi fingerprint melalui akses server yang dipercaya sebelum menyimpannya.
   Port nonstandar memakai bentuk `[host]:port` di known_hosts.
5. Untuk setiap akun Codespaces, simpan token akun tersebut pada nama secret yang berbeda.
   Contoh paling sederhana: PAT classic dengan scope `codespace`, milik akun yang
   memiliki Codespace dan diizinkan oleh kebijakan organisasi. Token bawaan
   `GITHUB_TOKEN` workflow hanya dipakai untuk mengambil repo dan membuat release.
6. Setelah perubahan nanti dipush ke default branch, pilih **Actions → Server health
   keepalive → Run workflow**. Implementasi ini belum menjalankan workflow atau push.

Contoh referensi:

```yaml
password: secret:MY_SERVER_PASSWORD
privatekey: env:MY_LOCAL_SSH_KEY
known_hosts: secret:MY_SERVER_KNOWN_HOSTS
github_token: secret:MY_CODESPACES_TOKEN
```

`secret:NAME` membaca repository secrets melalui `HEALTH_SECRETS_JSON`.
`env:NAME` membaca environment proses collector, cocok untuk eksekusi lokal.
Repository secret tidak otomatis menjadi environment variable; jika ingin memakai
`env:NAME` di Actions, tambahkan pemetaan `NAME: ${{ secrets.NAME }}` di `env`
step **Collect health**. `var:NAME` juga didukung untuk Actions variables lewat
`HEALTH_VARS_JSON`, tetapi simpan password/key/token sebagai secrets.

Nama kredensial dinamis didukung tanpa mengubah workflow karena step collector
menerima `toJSON(secrets)`. Bundle ini hanya tersedia pada step tersebut dan tidak
diteruskan ke proses SSH/GH. Hanya token akun yang sedang diperiksa atau password
SSH terkait yang diberikan ke proses anak. Jangan memasukkan nilai secret langsung
ke YAML. Konfigurasi dan probe adalah kode tepercaya yang dieksekusi workflow.

Struktur akun mengikuti `servers.github-codespace.account1.codespace1.name`;
boleh menambah akun dan target sebanyak yang diperlukan. Semua contoh target
awalnya nonaktif. Tanpa target aktif, laporan menyatakan **No enabled targets**.

## Eksekusi dan pengukuran

Job berjalan dalam `alpine:3.23` di atas host runner `ubuntu-latest`. Alpine menginstal
`github-cli`, `openssh-client`, `sshpass`, Python, PyYAML, CA certificates dan tar.
Source diambil berdasarkan `GITHUB_SHA` melalui API arsip GitHub agar tidak bergantung
pada JavaScript action yang memerlukan glibc di Alpine.

`command: sh -s` di YAML dieksekusi di server, dengan isi `probe: scripts/health.sh`
dikirim lewat stdin. Ini setara dengan:

```sh
GH_TOKEN="$TOKEN_AKUN" gh cs ssh -c "$NAMA_CODESPACE" -- -T -o BatchMode=yes 'sh -s' < scripts/health.sh
```

Token dikirim lewat environment, bukan disisipkan ke command remote. `command`
bisa diganti dengan perintah shell remote dan `probe` bisa diarahkan ke skrip lain
(relatif terhadap lokasi YAML). Command kustom bertanggung jawab menghasilkan metrik
sendiri. Exit nonzero dianggap gagal. SSH memakai verifikasi host key ketat.

Target harus Linux dengan `/proc`, `sh`, `awk`, `sleep`, dan `ps` dari procps
(`apt install procps` pada Debian/Ubuntu; `apk add procps` pada Alpine).
Codespaces harus memiliki SSH server; lihat [manual gh codespace ssh](https://cli.github.com/manual/gh_codespace_ssh).

- Uptime: detik dan hari dari `/proc/uptime`.
- Memori: `MemTotal - MemAvailable`, MiB dan persen.
- CPU: selisih counter `/proc/stat` selama 1 detik; idle termasuk iowait.
- Top 5: proses yang masih ada, diurutkan `%CPU` dari `ps` (rata-rata selama umur
  proses, bukan sampel 1 detik). Proses sleeping ikut dihitung. Ditampilkan PID,
  nama executable, CPU%, MEM%; argumen command line tidak ditampilkan.

Pada container/Codespaces, CPU/memori/uptime adalah tampilan host melalui `/proc`,
bukan kuota cgroup container. Daftar proses mengikuti PID namespace yang terlihat.

Setiap target memiliki batas waktu `timeout_seconds` (default 180, maksimum 600).
Timeout mematikan grup proses lokal SSH/GH. Target diperiksa berurutan; kegagalan
satu target tidak menghentikan target lain. Sesuaikan timeout job 45 menit jika
jumlah target besar. Timeout/pembatalan seluruh job bisa mencegah penerbitan laporan.

## Report ke Releases

Setiap run membuat release `health-<run-id>-<attempt>`, dengan isi laporan sebagai
release notes dan dua asset: `health.md` serta `health.json`. JSON menyimpan status
serta output teks probe per target. Release menunjuk commit yang diperiksa dan tidak
dijadikan latest release. Dibutuhkan permission workflow `contents: write`.
Laporan juga muncul di Actions job summary. Jika ada target gagal, report tetap
diterbitkan terlebih dahulu, lalu workflow ditandai gagal.

Release mengikuti visibilitas repository dan memuat label server serta nama proses;
pakai label yang sesuai untuk dibagikan. Nilai secret yang diketahui disensor dan
stderr koneksi tidak diterbitkan. Release baru dibuat setiap run, tanpa penghapusan
otomatis.

Cron GitHub dapat terlambat; scheduled workflow berjalan dari default branch.
Lihat [dokumentasi schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule).
Ini eksperimen pengecekan/keepalive berkala, bukan jaminan Codespace selalu hidup.
Codespaces memiliki idle timeout (default 30 menit); koneksi per jam tidak menjamin
aktivitas terus-menerus. Lihat [lifecycle Codespaces](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle).

## Validasi lokal

Install Python 3 dan PyYAML, serta `gh`, `ssh`, `sshpass` untuk koneksi aktual.

```sh
python3 -m unittest discover -s tests -v
python3 scripts/health.py --config servers.yaml --output reports
sh scripts/health.sh
```

Collector lokal hanya menulis laporan, tidak membuat release. Selama semua target
contoh nonaktif, perintah collector tidak membuat koneksi remote. `health.py`
menyimpan kegagalan di `failed` pada JSON; workflow melakukan pengecekan exit status
setelah publish. File laporan, `.env`, dan cache Python diabaikan Git.
