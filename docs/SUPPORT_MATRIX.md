# 지원 매트릭스

이 문서는 provider가 인식하는 범위와 실제로 검증된 범위를 구분한다. 패키지
이름을 알고 있거나 설치 계획을 만들 수 있다는 사실만으로 해당 조합을
지원한다고 간주하지 않는다.

## 현재 상태

**검증 완료(`verified`) 조합 수: 0**

기존 `init.sh`가 Ubuntu, XFCE, TigerVNC를 전제로 작성되어 있지만 깨끗한 VM에서
설치, 재실행, 재부팅, VNC 로그인, Guacamole 접속까지 통과했다는 자동화된 증거가
없다. 따라서 이 조합도 현재는 `experimental`이다.

지원 등급은 다음과 같다.

| 등급 | 의미 |
|---|---|
| `verified` | 정확한 OS `ID`/`VERSION_ID`, 데스크톱, VNC, 배포 방식 조합이 깨끗한 VM 종단 테스트를 통과했다. |
| `experimental` | provider 메타데이터와 검증 규칙은 있지만 종단 테스트가 없거나 외부 저장소·rolling release·compositor 등 변수가 남아 있다. |
| `unsupported` | 알려진 구조적 비호환이 있거나 필요한 명령·패키지·수명주기를 프로젝트가 정의하지 않는다. |

한 축이 `experimental`이면 전체 조합도 `experimental`이다. 각 축이 따로 동작한
사실을 조합 전체의 검증으로 간주하지 않는다.

## 플랫폼

| Provider | 인식하는 계열 | 패키지 관리자 | 기본 init | 현재 등급 | 주요 제한 |
|---|---|---|---|---|---|
| `debian` | Debian, Ubuntu 및 `ID_LIKE=debian` | `apt-get`/`apt-cache` | systemd | experimental | 릴리스별 Tomcat 버전 및 패키지 분할 차이. 파생판은 별도 검증 필요 |
| `rhel` | RHEL, CentOS, Rocky, AlmaLinux, Oracle Linux | `dnf` | systemd | experimental | subscription, CRB, EPEL 의존 가능. SELinux 정책 필요 |
| `fedora` | Fedora | `dnf` | systemd | experimental | 빠른 패키지 변화와 Plasma X11 패키지 분할 |
| `arch` | Arch 및 일부 Arch 파생판 | `pacman` | systemd | experimental | rolling release. 부분 업그레이드 금지. AUR은 지원 저장소로 간주하지 않음 |
| `suse` | openSUSE Leap/Tumbleweed, SLES | `zypper` | systemd | experimental | pattern 이름, VNC 패키지, display manager 및 nginx layout 차이 |
| `alpine` | Alpine | `apk` | OpenRC | experimental | musl/OpenRC 경로 미검증. systemd unit 사용 불가 |
| `custom` | 그 밖의 Linux | 사용자 정의 | 사용자 정의 | unsupported | package/service 명령과 검증 기준을 운영자가 제공해야 함 |

`ID`가 정확히 일치하면 그 결과를 우선하며, 그 다음 `/etc/os-release`의
`ID_LIKE`를 사용한다. 알려지지 않은 파생판을 상위 계열로 탐지할 수는 있지만
그 파생판이 자동으로 verified가 되지는 않는다.

### 패키지 명령

provider가 만드는 명령은 shell 문자열이 아니라 argv tuple이다.

| 관리자 | update | install prefix | package probe prefix |
|---|---|---|---|
| APT | `apt-get update` | `apt-get install -y --no-install-recommends` | `apt-cache show` |
| DNF | `dnf -y makecache` | `dnf -y install` | `dnf --quiet list --showduplicates` |
| Pacman | `pacman -Syu --noconfirm` | `pacman -S --needed --noconfirm` | `pacman -Si` |
| Zypper | `zypper --non-interactive refresh` | `zypper --non-interactive install --no-recommends` | `zypper --non-interactive search --match-exact --type package` |
| APK | `apk update` | `apk add --no-cache` | `apk search --exact` |

Arch에서는 `pacman -Sy`만 실행하는 부분 업그레이드를 허용하지 않는다. RHEL
계열에서는 EPEL, CRB 또는 제3자 저장소를 사용자 동의 없이 활성화하지 않는다.

## 데스크톱

모든 내장 launch command는 현재 X11 세션용이다. 설치 시에는 family별로 등록된
패키지 후보 묶음을 순서대로 probe하고, 한 묶음 전체가 존재할 때만 선택해야 한다.

| Provider | 세션 argv | X session 후보 | 현재 등급 | 비고 |
|---|---|---|---|---|
| `auto` | 자동 탐지 | `/usr/share/xsessions` | experimental | 정확히 하나의 알려진 X11 세션만 발견될 때 사용 가능 |
| `none` | 없음 | 없음 | experimental | 외부/shared VNC에는 가능하지만 virtual VNC desktop에는 불가 |
| `xfce` | `startxfce4` | `xfce`, `xfce4` | experimental | 첫 verified 승격 후보 |
| `gnome` | `gnome-session --session=gnome` | `gnome-xorg`, `gnome` | experimental | Wayland 기본값, D-Bus/user service, software rendering 검증 필요 |
| `kde` | `startplasma-x11` | `plasma-x11`, `plasma5`, `plasma` | experimental | 명시적인 Plasma X11 session package와 compositor 검증 필요 |
| `mate` | `mate-session` | `mate` | experimental | RHEL 계열은 EPEL 의존 가능 |
| `lxqt` | `startlxqt` | `lxqt` | experimental | 별도 X11 window manager가 필요할 수 있음 |
| `lxde` | `startlxde` | `lxde` | experimental | 최신 배포판에서 저장소 가용성 probe 필요 |
| `cinnamon` | `cinnamon-session` | `cinnamon`, `cinnamon2d` | experimental | Muffin compositor 및 software rendering 검증 필요 |
| `custom` | 사용자 정의 | 사용자 정의 | unsupported | 명시적인 session argv 없이는 사용할 수 없음 |

Wayland session 파일은 `/usr/share/wayland-sessions`에 있으며 TigerVNC/TightVNC의
virtual X11 세션으로 자동 선택하지 않는다. X startup은 세션 명령 앞에
`exec dbus-run-session --`를 사용하고 `DISPLAY`를 임의로 덮어쓰지 않는 구성을
전제로 한다.

## VNC 구현

| Provider | 동작 모드 | package 예시 | display 호환성 | 기본 포트 | 현재 등급 |
|---|---|---|---|---|---|
| `tigervnc` | 격리된 virtual desktop | Debian `tigervnc-standalone-server`; RPM `tigervnc-server`; Arch `tigervnc`; SUSE는 probe | 생성된 X11 | `5900 + display` | experimental |
| `tightvnc` | legacy virtual desktop | Debian `tightvncserver`; SUSE 후보 `tightvnc` | 생성된 X11 | `5900 + display` | experimental, 제한적 |
| `x11vnc` | 기존 화면 shared | `x11vnc` | 실행 중인 Xorg만 | 5900 | experimental |
| `wayvnc` | 기존 화면 shared | `wayvnc` | wlroots 호환 Wayland compositor | 5900 | experimental |
| `external` | 프로젝트 외부 endpoint | 없음 | 외부 서버 책임 | 명시 필수 | experimental |
| `custom` | 사용자 정의 | 사용자 정의 | 사용자 정의 | 사용자 정의 | unsupported |

TigerVNC의 명령 이름은 배포판에 따라 `tigervncserver` 또는 `vncserver`, 암호
도구는 `tigervncpasswd` 또는 `vncpasswd`일 수 있다. 설치 후 binary를 다시
resolve하여 절대 경로로 저장해야 한다. native unit도
`vncserver@.service`/`tigervncserver@.service` 또는 별도 layout일 수 있으므로
정적 경로를 가정하지 않는다.

`x11vnc`와 `wayvnc`는 데스크톱을 생성하지 않는다. 이미 로그인된 graphical
session 뒤에서 시작되어야 하며 virtual VNC와 같은 service/xstartup 흐름을 쓰지
않는다. TigerVNC, TightVNC, x11vnc는 외부 네트워크에 직접 노출하지 않고
localhost listener를 검증하는 것이 기본 보안 경계다.

현재 built-in apply에서 wayvnc는 wlroots user-session lifecycle이 없어 차단되며,
x11vnc의 password 생성은 secret을 프로세스 인자에 노출하지 않는 portable 경로가
없어 차단된다. x11vnc는 기존 Xorg 세션에서 loopback-only `authentication=none`
또는 별도 검토한 custom rfbauth provider로만 계획할 수 있다.

Budgie, 상용 RealVNC, GNOME Remote Desktop, KDE `krfb`는 요청을 조용히 다른
backend로 바꾸지 않도록 registry에 reserved/unsupported provider로 등록되어 있다.
이 항목들은 third-party 저장소, 라이선스 또는 실제 사용자 세션 설정을 추측하지
않으며 전용 provider가 구현·검증되기 전에는 적용을 거부한다.

## 명시적으로 unsupported인 조합

- `x11vnc` + 활성 Wayland 전체 화면. XWayland에 보이는 일부 창은 완전한 desktop
  공유가 아니다.
- `wayvnc` + 내장 GNOME/KDE/Cinnamon/XFCE/MATE/LXQt/LXDE session launcher.
  내장 launcher는 X11용이며 wayvnc는 일반 Wayland 서버가 아니라 wlroots 호환
  compositor가 필요하다.
- `tightvnc` + GNOME/KDE/Cinnamon virtual desktop.
- 공식 저장소만 허용한 RHEL/Fedora/Arch/Alpine + TightVNC. Arch AUR 패키지는
  native supported dependency로 취급하지 않는다.
- virtual VNC + `desktop=none`.
- 명령을 제공하지 않은 `custom` platform/desktop/VNC.
- systemd unit을 기본 OpenRC Alpine에 그대로 설치하는 구성.

## 우선 검증할 후보

다음은 **verified 목록이 아니라** 테스트 우선순위다.

1. Ubuntu LTS 또는 Debian stable + TigerVNC + XFCE + hybrid Compose 배포
2. 같은 플랫폼 + TigerVNC + MATE/LXQt
3. Fedora + TigerVNC + XFCE
4. openSUSE/Arch + TigerVNC + XFCE
5. 명시적 Xorg login + x11vnc shared session

GNOME, KDE, Cinnamon은 compositor와 X11 session 패키지 때문에 후순위다. RHEL
계열의 비-GNOME 데스크톱은 EPEL/CRB 정책을 먼저 결정해야 한다. TightVNC는
호환성 확대의 주 경로가 아니라 legacy opt-in 경로로 유지한다.

## verified 승격 조건

정확한 tuple은 다음을 모두 통과해야 `verified`로 승격할 수 있다.

1. 지원하려는 정확한 OS `ID`와 `VERSION_ID`의 깨끗한 VM에서 설치
2. 같은 설정으로 두 번째 실행이 무해한지 확인
3. 재부팅 후 필요한 서비스가 정상 상태인지 확인
4. VNC가 localhost에만 listening하고 인증 없이 접속되지 않는지 확인
5. 실제 VNC 로그인 후 선택한 window manager와 D-Bus user session 확인
6. Guacamole을 통한 브라우저 접속과 키보드·클립보드·화면 갱신 확인
7. 제거 후 프로젝트 소유 파일만 사라지고 기존 display manager/desktop이 보존되는지 확인

rolling release는 이 검증을 정기적으로 다시 수행해야 한다. 검증 기록에는 이미지
버전, 패키지 버전, architecture, 실행 일자와 테스트 결과를 남겨야 한다.

## 검증 API

```python
from urd_installer.providers import validate_combination

result = validate_combination(system_facts, installer_config)
if not result.ok:
    for error in result.errors:
        print(error)
for warning in result.warnings:
    print(warning)
print(result.tier, result.reasons)
```

`facts`는 attribute object 또는 mapping을 받을 수 있다. `config`도
`InstallerConfig`, attribute object 또는 mapping을 받을 수 있다. 반환값은
`errors`, `warnings`, `tier`, `reasons`, `ok`를 분리하며 `as_dict()`로 CLI 출력에
사용할 수 있다. `require_verified=true`인 설정은 현재 모든 조합을 거부하는 것이
정상 동작이다.
