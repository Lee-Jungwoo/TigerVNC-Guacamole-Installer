# 보안 모델

이 설치기는 호스트의 패키지, 서비스, 컨테이너, 프록시 설정을 변경하므로 실행 자체가 관리자 권한 작업입니다. 적용 전에는 반드시 설정 스키마 검증과 dry-run을 수행하고, 운영 호스트의 설정 및 데이터베이스를 별도로 백업해야 합니다.

## 위협 모델

보호 대상은 로그인 자격증명, VNC 세션, Guacamole 데이터베이스, TLS 개인 키, 기존 시스템 설정입니다. 다음 위험을 방어 범위에 포함합니다.

- 인터넷에서 VNC, guacd, 데이터베이스 또는 웹 애플리케이션 포트로 직접 접근하는 공격자
- 같은 호스트의 비권한 사용자가 프로세스 인자, 로그 또는 허술한 파일 권한을 통해 비밀을 읽는 경우
- 변조되거나 손상된 다운로드, 컨테이너 태그 변경, 잘못된 저장소 혼합
- 악의적인 hostname/경로/옵션 입력, 반복 실행, 설치 중 실패로 인한 기존 설정 손상

이미 root 권한을 획득한 공격자, 손상된 커널·컨테이너 런타임, 사용자가 명시적으로 신뢰한 custom provider의 악성 코드는 방어 범위 밖입니다. `custom.enabled`를 켜면 `custom.provider_directory` 아래 코드를 root 권한 코드와 동일하게 검토해야 합니다.

## 안전한 기본값

- 기본 프로필은 `hybrid`, 데스크톱/VNC 권장 조합은 XFCE와 TigerVNC입니다.
- VNC와 Guacamole 웹은 `127.0.0.1`에만 바인딩합니다. guacd와 PostgreSQL도 외부에 게시하지 않습니다.
- 프록시도 기본적으로 loopback에서만 수신하고 TLS는 꺼져 있습니다. 이 상태는 로컬 테스트나 외부 TLS 종료 장치 뒤에서만 사용해야 합니다.
- 방화벽 기본 모드는 `none`이며 기존 규칙을 변경하지 않습니다. SSH 설정, 사용자 암호, PAM 및 sudo 정책도 변경하지 않습니다.
- 설치 비밀번호나 범용 기본 비밀번호를 제공하지 않습니다. Guacamole의 초기 관리 계정은 외부 공개 전에 반드시 고유한 비밀번호로 설정해야 합니다.

## 비밀 파일 계약

[`examples/config.json`](examples/config.json)에는 비밀 값이 아니라 비밀을 읽을 절대 경로만 기록합니다. 데이터베이스, VNC, Guacamole 관리자, ACME DNS 자격증명 모두 같은 규칙을 따릅니다.

- 비밀 파일은 일반 파일이어야 하며 심볼릭 링크를 허용하지 않습니다. 소유자는 root 또는 설치를 실행한 신뢰된 사용자이고 권한은 `0600`이어야 합니다.
- 일회성 설치 비밀은 `/run/secrets`처럼 메모리 기반이며 재부팅 시 사라지는 위치를 권장합니다. 영구 파일이 필요하면 root 전용 디렉터리에 두고 백업·회전 정책을 별도로 적용합니다.
- 비밀은 명령행 인자, 환경 변수, shell trace, dry-run, 상태 파일 또는 로그에 출력하지 않습니다. 모든 진단 출력은 `[REDACTED]`로 대체해야 합니다.
- 데이터베이스 관리자 인증은 가능한 경우 로컬 Unix socket을 사용합니다. 비밀번호를 `psql`/`mysql` 명령행 옵션에 전달하지 않습니다.
- 서비스가 런타임에 평문 설정을 요구하면 해당 파일은 `0640 root:<service-group>`, 부모 디렉터리는 `0750` 이하로 제한합니다. Compose의 `secrets`도 원본 호스트 파일을 자동으로 암호화하지 않는다는 점에 유의합니다.
- 임시 파일은 `umask 077`로 만들고 종료 trap에서 즉시 삭제합니다. SSD나 CoW 파일시스템에서는 `shred`가 안전한 삭제를 보장하지 않습니다.

## 네트워크 포트

| 포트 | 용도 | 기본 노출 |
| --- | --- | --- |
| 5901 | VNC display `:1` | `127.0.0.1`만 |
| 4822 | guacd | 전용 컨테이너 네트워크 또는 loopback만 |
| 8080 | Guacamole/Tomcat | `127.0.0.1`만 |
| 5432 | PostgreSQL | managed Compose에서는 host loopback만 |
| 80/443 | 선택한 reverse proxy | 기본은 loopback, 명시적 공개 설정 시에만 외부 노출 |

guacd는 자체 인증을 제공하지 않으므로 절대 신뢰되지 않은 네트워크에 공개하면 안 됩니다. VNC도 기본적으로 암호화된 인터넷 프로토콜로 간주하지 않으며 Guacamole 또는 별도의 보안 터널을 통해서만 접근해야 합니다. `allow_vnc`는 특별한 폐쇄망 요구가 아니면 켜지 마십시오.

## 컨테이너, 프록시 및 TLS

컨테이너 배포는 `latest` 태그를 사용하지 않고 [`versions/lock.json`](versions/lock.json)의 1.6.0 태그를 플랫폼별 OCI digest로 해석해 고정해야 합니다. 격리 network를 쓰거나 hybrid host-network 경로에서는 4822/5432를 명시적으로 loopback에 bind하며, Docker/Podman socket을 컨테이너에 마운트하지 않습니다. 가능한 범위에서 non-root 사용자, 최소 capability, read-only 파일시스템과 SELinux/AppArmor 격리를 적용하고 영구 데이터 볼륨의 권한과 백업을 점검합니다.

Nginx, Apache, Caddy 또는 Traefik 설정은 기존 기본 사이트를 삭제하지 않고 별도 관리 파일로 생성합니다. 활성화 전에 각 제품의 config validator를 통과해야 하며 실패 시 기존 설정을 유지합니다. WebSocket과 장시간 연결을 지원하되 `X-Forwarded-*` 헤더는 `trusted_proxy_cidrs`에서 온 요청에만 신뢰합니다.

TLS 모드는 다음과 같이 구분합니다.

- `acme-http-01`: DNS A/AAAA, 80번 포트 접근성 및 포트 충돌을 사전 확인합니다.
- `acme-dns-01`: DNS API 자격증명을 `0600` 비밀 파일로 전달합니다.
- `existing`: 인증서 SAN, 만료일, 개인 키 일치 여부와 파일 권한을 검증합니다.
- `self-signed`: 개발 및 폐쇄망 테스트에만 사용합니다.
- `external`: 외부 로드 밸런서나 프록시가 TLS 종료와 갱신을 책임집니다.
- `off`: 모든 HTTP 수신 주소가 loopback일 때만 안전한 기본값입니다.

ACME는 먼저 staging에서 검증하고 운영 전환 시 이용약관 동의를 명시해야 합니다. HTTPS가 정상 동작하고 복구 경로가 확인되기 전에는 HSTS를 켜지 않습니다. 인증서 갱신 작업은 실제 프록시 reload와 만료 모니터링까지 검증해야 합니다.

## 공급망 검증

Apache Guacamole 1.6.0의 공식 [릴리스 페이지](https://guacamole.apache.org/releases/1.6.0/)는 각 파일에 OpenPGP 서명과 SHA-256 manifest를 제공합니다. 설치기는 다음 검증을 모두 통과하지 못하면 중단해야 합니다.

1. HTTPS로 아티팩트, `.sha256`, `.asc`, Apache Guacamole `KEYS`를 권한이 제한된 임시 디렉터리에 받습니다.
2. manifest의 파일명이 정확히 일치하는지 확인한 뒤 SHA-256을 검증합니다.
3. 공개 keyserver를 사용하지 않고 격리된 임시 keyring에서 detached signature를 검증합니다. 무인 운영 전에는 릴리스 서명자 fingerprint를 별도 채널로 검토해 고정합니다.
4. 검증 전에는 root로 압축을 풀거나 실행하지 않으며, archive path traversal과 심볼릭 링크를 거부합니다.

검토되지 않은 checksum이나 OCI digest를 만들어 넣어서는 안 됩니다. 배포판이 다른 저장소를 섞거나 지원 종료된 패키지를 높은 우선순위로 pin하는 것도 금지합니다.

## 알려진 제한

- 모든 Linux/데스크톱/VNC 조합이 기술적으로 호환되는 것은 아닙니다. Xvnc 계열은 가상 X11 세션, x11vnc는 기존 X11 세션, wayvnc는 주로 wlroots Wayland에 해당합니다. GNOME/KDE Wayland 및 상용 RealVNC는 별도 provider·정책·라이선스가 필요합니다.
- 지원 매트릭스에 없는 조합은 추측해 설치하지 않고 명시적으로 실패해야 합니다. `distribution: custom`은 보안 우회가 아니라 검토된 provider를 연결하는 확장점입니다.
- 전통적인 VNC password 인증은 구현에 따라 길이·암호학적 제한이 있습니다. 강한 임의 비밀번호와 loopback 바인딩을 함께 사용해도 TLS나 네트워크 격리를 대체하지 못합니다.
- 로컬 방화벽을 관리하지 않는 기본값은 클라우드 보안 그룹, 라우터 ACL 또는 외부 방화벽을 구성해 주지 않습니다.
- 오디오, clipboard, 파일 전송, 인쇄, 세션 녹화는 데이터 유출 또는 민감 정보 저장 경로가 될 수 있어 모두 명시적으로 활성화해야 합니다.

보안 문제가 Apache Guacamole 자체에 해당하면 Apache의 [Security Reports](https://guacamole.apache.org/security/) 절차를 따르십시오.
