# Universal Remote Desktop Installer

Linux 데스크톱, VNC 서버, Apache Guacamole를 하나의 고정된 Ubuntu 스크립트가
아니라 **감지 + provider + compatibility validation + plan/apply/verify** 방식으로
구성하는 설치기입니다.

> “모든 Linux 조합”이 실제로 호환된다는 뜻은 아닙니다. 예를 들어 x11vnc는
> Wayland 전체 화면을 공유할 수 없고, wayvnc는 일반 GNOME/KDE 서버가 아닙니다.
> 이 프로젝트는 지원할 수 없는 조합을 임의로 바꾸지 않고 이유와 함께 차단하며,
> 검증되지 않은 경로를 `experimental`로 표시합니다.

기존 `init.sh`의 `ubuntu` 사용자, apt, XFCE, TigerVNC, Tomcat 9, MariaDB 고정값과
기본 암호, `chmod 777`, 계정 암호 삭제, 무검증 다운로드, raw iptables 변경은
제거되었습니다.

## 현재 범위

- 플랫폼 provider: Debian/Ubuntu 계열, RHEL/Rocky/AlmaLinux 계열, Fedora,
  Arch/Manjaro, openSUSE/SLES, Alpine, custom 확장점
- 데스크톱 provider: XFCE, GNOME Xorg, KDE Plasma X11, MATE, LXQt, LXDE,
  Cinnamon, auto/none/custom
- VNC provider: TigerVNC, TightVNC, x11vnc, wayvnc, external/custom
- 명시적 reserved provider: Budgie, RealVNC, GNOME Remote Desktop, KDE krfb
- Guacamole 배포: Docker Compose 또는 Podman Compose 기반 `hybrid`,
  `vnc-only`, 외부 Guacamole 연동
- 프록시 renderer: Nginx, Apache, Caddy, none/external
- init: systemd 및 OpenRC
- JSON Schema v1 설정, secret-file 계약, dry-run, atomic file install/backup,
  상태 잠금, doctor/verify, 보존형 uninstall

정확한 등급과 제한은 [지원 매트릭스](docs/SUPPORT_MATRIX.md), 위협 모델과 포트
정책은 [보안 문서](SECURITY.md)를 확인하십시오. 현재 clean VM 종단 시험으로
`verified`에 승격된 조합은 0개이며 built-in 경로는 보수적으로
`experimental`입니다.

## 요구 사항

- Linux 호스트
- Python 3.9 이상
- 패키지를 설치할 root 또는 sudo 권한
- `hybrid` 프로필은 Docker/Podman Compose를 설치할 수 있는 저장소
- 완전한 저장소 checkout (`init.sh` 한 파일만 내려받는 방식은 지원하지 않음)

Python이 없다면 bootstrap에 명시적으로 설치를 허용할 수 있습니다.

```bash
./init.sh --install-python detect
```

## 빠른 시작

### 1. 저장소와 설정 준비

```bash
git clone https://github.com/Lee-Jungwoo/TigerVNC-Guacamole-Installer.git
cd TigerVNC-Guacamole-Installer
cp examples/config.json config.json
```

`config.json`의 `target.desktop_user`를 실제 기존 사용자로 바꾸거나,
`create_user: true`로 전용 사용자를 만들도록 설정합니다.

### 2. secret 파일 준비

예제 설정은 암호 자체가 아니라 `/run/secrets` 아래 파일을 참조합니다.

```bash
sudo install -d -m 0700 /run/secrets
openssl rand -hex 4 | sudo tee /run/secrets/tvgi_vnc_password >/dev/null
openssl rand -base64 36 | sudo tee /run/secrets/tvgi_postgresql_password >/dev/null
openssl rand -base64 24 | sudo tee /run/secrets/tvgi_guacamole_admin_password >/dev/null
sudo chmod 0600 /run/secrets/tvgi_*
```

VNC legacy 인증은 실효 길이가 최대 8자이므로 VNC secret만 정확히 8자의 printable
ASCII여야 합니다. DB와 Guacamole 관리자 secret은 16자 이상이어야 합니다.
명령행 인자나 JSON에 평문 암호를 넣지 마십시오.

설정에서 secret 경로를 비워 두면 적용 시 `/opt/urd/secrets`에 root-only 임의값이
생성됩니다. 실제 경로는 `target.install_root`로 변경할 수 있습니다.

### 3. 감지, 계획, 적용

```bash
./init.sh detect
./init.sh list-supported
./init.sh plan --config config.json
./init.sh apply --config config.json --dry-run --yes
sudo ./init.sh apply --config config.json --yes
./init.sh verify --config config.json
```

일반 사용자로 실행해도 필요한 단계만 sudo를 사용합니다. CI에서는 전역 옵션을
명령 앞이나 뒤에 둘 수 있습니다.

```bash
./init.sh --json --non-interactive apply --config config.json --yes
```

## 명령

| 명령 | 동작 |
| --- | --- |
| `detect` | OS, init, 패키지 관리자, architecture, X11 세션을 읽기 전용 감지 |
| `list-supported` | provider와 verified/experimental/unsupported 등급 출력 |
| `plan` | 조합 검증 후 실행할 desired-state 단계 출력; 시스템 변경 없음 |
| `apply` | 승인된 plan 적용; 파일은 비교 후 atomic 교체 |
| `verify` | 사용자, VNC 서비스, Compose, HTTP 상태 확인 |
| `doctor` | 충돌 포트, 필요한 도구, 조합 제한 진단 |
| `uninstall` | 관리 서비스 정지; desktop 패키지와 DB 데이터는 기본 보존 |

공통 옵션:

```text
--config PATH       JSON Schema v1 설정
--dry-run           쓰기, sudo, network, service 호출 없이 계획만 시뮬레이션
--yes               적용/제거 승인
--non-interactive   prompt 금지
--json              machine-readable 출력
```

## 프로필

### `hybrid` (권장, experimental)

호스트에 선택한 desktop/VNC를 설치하고 Apache Guacamole 1.6.0, guacd,
PostgreSQL 16을 Compose로 배포합니다. Tomcat과 데이터베이스 패키지 차이를
컨테이너 안으로 격리하면서 실제 호스트 데스크톱을 제공합니다.

- VNC, guacd, PostgreSQL은 host loopback에만 bind됩니다.
- Guacamole 컨테이너는 공식 `entrypoint.d` 확장을 통해 Tomcat을 설정의
  `web_bind_address`에 bind합니다.
- DB 암호는 Compose secret file로 전달됩니다.
- Docker와 Podman stack은 root-owned secret/volume 경계를 일관되게 유지하기 위해
  현재 rootful Compose로 관리됩니다. rootless Podman은 별도 provider가 필요합니다.
- 공식 이미지의 `initdb.sh --postgresql`로 schema를 생성합니다.
- schema가 만드는 `guacadmin`의 기본 암호는 시작 직후 설정한 관리자 secret의
  salted SHA-256 값으로 교체됩니다.
- `Local desktop (<user>)` VNC connection을 idempotent SQL로 생성합니다.

### `vnc-only`

desktop/VNC와 서비스만 관리합니다. Guacamole, DB, proxy는 설치하지 않습니다.

### `external`

로컬 desktop/VNC endpoint만 관리하고 Guacamole/DB/TLS는 외부 시스템이
책임집니다.

### `native`

설정 스키마와 provider 확장점은 존재하지만 현재 built-in apply는 중단됩니다.
Tomcat/Java/JDBC/build dependency가 배포판 버전마다 달라 실제 VM 검증 없이 다른
릴리스 패키지를 섞는 방식은 허용하지 않습니다.

## 접속

기본 URL은 호스트 내부에서 다음과 같습니다.

```text
http://127.0.0.1:8080/guacamole/
```

원격에서 안전하게 먼저 확인하려면 SSH tunnel을 사용합니다.

```bash
ssh -L 8080:127.0.0.1:8080 user@server
```

브라우저에서 `http://127.0.0.1:8080/guacamole/`로 접속하고 사용자명
`guacadmin`, 암호는 `guacamole.admin_password_file`의 값을 사용합니다. 공용
서비스는 외부 TLS reverse proxy/로드 밸런서와 network policy를 먼저 구성해야
합니다.

관리자 암호는 desired state이므로 웹 UI에서만 바꾼 뒤 같은 설정으로 다시
`apply`하면 secret 파일의 값으로 되돌아갑니다. 회전할 때는 secret 파일도 함께
갱신하십시오.

PostgreSQL secret을 바꾸면 installer가 로컬 DB role 암호를 먼저 reconcile한 뒤
Guacamole 컨테이너를 재시작하므로 기존 volume에서도 함께 회전됩니다.

로컬 VNC connection은 다음 매개변수로 provision됩니다.

```text
hostname: 127.0.0.1
port: 5900 + display_number (기본 5901)
password: vnc.password_file 또는 생성된 managed secret
```

## 설정 원칙

전체 예시는 [examples/config.json](examples/config.json), 기계 검증 규격은
[schemas/config-v1.schema.json](schemas/config-v1.schema.json)에 있습니다.

주요 축은 다음과 같습니다.

```text
profile
target.{distribution,os_family,container_runtime,desktop_user,install_root}
desktop.{environment,display_server,display_manager,session}
vnc.{implementation,mode,bind_address,display_number,port,authentication}
guacamole.{deployment,version,web_bind_address,context_path}
database.{engine,deployment,password_file}
proxy.{provider,deployment,listen_address,domain}
tls.mode
firewall.mode
```

설정 파일은 shell로 `source`하지 않으며, 중복 JSON key, unknown property, 잘못된
enum/IP/hostname/email/경로/포트, 조건부 필수값을 bundled JSON Schema로
검증합니다.

## 호환성에서 중요한 차이

- TigerVNC/TightVNC는 별도 virtual X11 display를 생성합니다.
- x11vnc는 이미 로그인된 Xorg `:0`을 공유하며 desktop을 새로 시작하지 않습니다.
- wayvnc는 wlroots-compatible Wayland compositor 안에서 실행되어야 합니다.
- GNOME/KDE/Cinnamon virtual session은 compositor와 X11 split package 때문에
  experimental입니다.
- display manager는 virtual VNC에 필요하지 않아 자동 교체하지 않습니다.
- RHEL desktop/x11vnc는 EPEL/CRB가 필요할 수 있지만 installer가 third-party
  저장소를 묵시적으로 활성화하지 않습니다.
- TightVNC의 비-Debian 경로와 AUR 의존 패키지는 자동 지원하지 않습니다.
- SELinux/AppArmor를 끄지 않습니다.

## 생성 리소스

기본 경로:

```text
/opt/urd/compose.yaml
/opt/urd/initdb/
/opt/urd/secrets/
/etc/systemd/system/urd-vnc-<user>.service
/usr/local/libexec/urd-vnc-session
~/.config/urd/vnc.conf
~/.config/urd/vnc.passwd
~/.vnc/xstartup
/var/lib/urd-installer/state.json
```

테스트나 비-root plan에서는 상태 경로를 바꿀 수 있습니다.

```bash
URD_STATE_DIR="$PWD/state" URD_LOCK_PATH="$PWD/state/lock" ./init.sh plan
```

## 운영과 제거

```bash
./init.sh doctor --config config.json
./init.sh verify --config config.json
sudo ./init.sh uninstall --config config.json --yes
```

`uninstall`은 서비스를 멈추되 PostgreSQL volume, desktop/VNC 패키지, 사용자 home,
managed 설정을 보존합니다. 이 데이터는 state와 백업을 검토한 후 운영자가 별도로
삭제해야 합니다. 기존 display manager, 로그인 암호, PAM/sudo 설정은 설치와 제거
모두 건드리지 않습니다.

## 개발 검증

네트워크나 root 없이 전체 테스트를 실행할 수 있습니다.

```bash
PYTHONPATH=src:. python3 -m unittest discover -s tests -v
bash -n init.sh
sh -n bootstrap.sh
python3 -m compileall -q src
git diff --check
```

CI는 Python 3.9/3.11/3.13에서 schema, provider matrix, injection 경계, secret
redaction, atomic/idempotent file 처리, dry-run 무변경, CLI JSON을 검사합니다.

## Upstream 기준

- [Apache Guacamole 1.6.0 release](https://guacamole.apache.org/releases/1.6.0/)
- [공식 container 설치 문서](https://guacamole.apache.org/doc/1.6.0/gug/guacamole-docker.html)
- [공식 PostgreSQL authentication 문서](https://guacamole.apache.org/doc/1.6.0/gug/postgresql-auth.html)

버전과 검증 전략은 [versions/lock.json](versions/lock.json)에 고정되어 있습니다.
OCI tag는 architecture별 digest를 apply 전에 해석·기록하는 단계가 아직 남아 있으므로
운영 승격 전 별도 digest pin이 필요합니다.
