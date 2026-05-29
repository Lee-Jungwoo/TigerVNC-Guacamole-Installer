# Guacamole VNC Setup Script

Ubuntu 22.04에서 Apache Guacamole, TigerVNC, XFCE 기반 원격 데스크톱 환경을 자동으로 설치하는 스크립트입니다.

## Features

* Apache Guacamole 1.5.5 설치
* TigerVNC Server 설정
* XFCE Desktop 설치
* Nginx reverse proxy 설정
* MariaDB 기반 Guacamole DB 설정
* Let's Encrypt SSL 인증서 발급
* Firefox, VS Code 설치
* 한글 폰트 및 한글 입력기 설치
* Firefox, VS Code 데스크톱 바로가기 생성

## Requirements

* Ubuntu 22.04 LTS
* sudo 권한
* 서버에 연결된 도메인 또는 hostname
* Let's Encrypt 인증서 발급용 이메일

## Installation

### 1. Download

```bash
wget -L https://raw.github.com/lee-jungwoo/Guacamole-setup/main/init.sh
```

또는 저장소를 clone합니다.

```bash
git clone https://github.com/Lee-Jungwoo/Guacamole-setup.git
cd Guacamole-setup
```

### 2. Make executable

```bash
chmod +x init.sh
```

필요하면 `sudo`를 사용합니다.

```bash
sudo chmod +x init.sh
```

### 3. Run

```bash
./init.sh
```

설치 중 hostname과 이메일을 입력합니다.

## Access

설치가 끝나면 아래 주소로 접속합니다.

```text
https://[your-domain]/guacamole
```

기본 계정은 다음과 같습니다.

```text
Username: guacadmin
Password: guacadmin
```

첫 로그인 후 비밀번호를 변경하세요.

## Default Settings

### MariaDB

```text
Database: guac_db
User: guac_user
Password: 1234
Root password: 1234
```

### VNC

```text
Display: :1
Port: 5901
Password: 123456
Desktop: XFCE4
```

### Network

```text
HTTP: 80
HTTPS: 443
Guacamole: 8080
VNC: 5901
```

VNC 포트는 localhost 연결을 기준으로 사용합니다.

## Service Management

서비스 상태 확인:

```bash
sudo systemctl status nginx guacd tomcat9 mysql
```

서비스 재시작:

```bash
sudo systemctl restart nginx guacd tomcat9 mysql
```

VNC 서버 확인:

```bash
tigervncserver -list
```

## Troubleshooting

### SSL certificate fails

도메인이 서버 IP를 가리키는지 확인합니다.

### VNC does not connect

VNC 서버가 실행 중인지 확인합니다.

```bash
tigervncserver -list
```

### Web page does not load

Nginx, Tomcat, Guacamole, MySQL 상태를 확인합니다.

```bash
sudo systemctl status nginx tomcat9 guacd mysql
```

필요하면 재시작합니다.

```bash
sudo systemctl restart nginx tomcat9 guacd mysql
```

### Database connection error

MariaDB가 실행 중인지 확인합니다.

```bash
sudo systemctl status mysql
```

## Logs

```text
Nginx: /var/log/nginx/
Tomcat: /var/log/tomcat9/
Guacamole: /var/log/tomcat9/catalina.out
MySQL: /var/log/mysql/
```

## Security Notes

* 기본 비밀번호를 설치 후 변경하세요.
* MySQL root password와 VNC password는 기본값이 약합니다.
* 스크립트는 `ubuntu` 사용자 비밀번호를 제거합니다.
* 운영 환경에서는 방화벽, 계정, DB 비밀번호 설정을 다시 검토하세요.
* Certbot 인증서는 자동 갱신됩니다.

## Note

이 스크립트는 개발 및 테스트 환경을 기준으로 작성되었습니다. 운영 환경에서 사용하려면 보안 설정을 수정하세요.

## Disclaimer

사용 중 발생하는 법적, 기술적 문제에 대한 책임은 사용자에게 있습니다.
