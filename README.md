# Guacamole VNC Setup Script

Ubuntu 22.04에서 Apache Guacamole, TigerVNC, XFCE 기반 원격 데스크톱 환경을 자동으로 설치하는 스크립트입니다.

## Features

- Apache Guacamole 1.5.5
- TigerVNC Server + XFCE Desktop
- Nginx reverse proxy
- MariaDB 기반 Guacamole DB
- Let's Encrypt SSL 인증서 발급
- Firefox, VS Code 설치 및 데스크톱 바로가기 생성
- 한글 폰트 및 한글 입력기

## Requirements

- Ubuntu 22.04 LTS
- sudo 권한
- 서버에 연결된 도메인 또는 hostname
- Let's Encrypt 인증서 발급용 이메일

## Installation

```bash
# 1. Download (또는 git clone https://github.com/Lee-Jungwoo/Guacamole-setup.git)
wget -L https://raw.github.com/lee-jungwoo/Guacamole-setup/main/init.sh

# 2. Make executable (필요하면 sudo 사용)
chmod +x init.sh

# 3. Run
./init.sh
```

설치 중 hostname과 이메일을 입력합니다.

## Access

`https://[your-domain]/guacamole` 로 접속합니다.

| Username | Password |
| --- | --- |
| `guacadmin` | `guacadmin` |

첫 로그인 후 비밀번호를 변경하세요.

## Default Settings

| 구분 | 설정 |
| --- | --- |
| MariaDB | Database `guac_db` · User `guac_user` · Password `1234` · Root password `1234` |
| VNC | Display `:1` · Port `5901` · Password `123456` · Desktop XFCE4 |
| Network | HTTP `80` · HTTPS `443` · Guacamole `8080` · VNC `5901` (localhost 연결 기준) |

## Service Management

```bash
sudo systemctl status nginx guacd tomcat9 mysql    # 상태 확인
sudo systemctl restart nginx guacd tomcat9 mysql   # 재시작
tigervncserver -list                               # VNC 서버 확인
```

## Troubleshooting

| 증상 | 확인 방법 |
| --- | --- |
| SSL certificate fails | 도메인이 서버 IP를 가리키는지 확인 |
| VNC does not connect | `tigervncserver -list`로 VNC 서버 실행 여부 확인 |
| Web page does not load | Nginx, Tomcat, Guacamole, MySQL 상태 확인 후 필요하면 재시작 (위 명령 참고) |
| Database connection error | `sudo systemctl status mysql`로 MariaDB 실행 여부 확인 |

## Logs

| 서비스 | 경로 |
| --- | --- |
| Nginx | `/var/log/nginx/` |
| Tomcat | `/var/log/tomcat9/` |
| Guacamole | `/var/log/tomcat9/catalina.out` |
| MySQL | `/var/log/mysql/` |

## Security Notes

- 기본 비밀번호를 설치 후 변경하세요. (MySQL root password와 VNC password는 기본값이 약합니다.)
- 스크립트는 `ubuntu` 사용자 비밀번호를 제거합니다.
- 운영 환경에서는 방화벽, 계정, DB 비밀번호 설정을 다시 검토하세요.
- Certbot 인증서는 자동 갱신됩니다.

> **Note** 이 스크립트는 개발 및 테스트 환경을 기준으로 작성되었습니다. 운영 환경에서 사용하려면 보안 설정을 수정하세요.
>
> **Disclaimer** 사용 중 발생하는 법적, 기술적 문제에 대한 책임은 사용자에게 있습니다.
