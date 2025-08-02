sudo apt update
sudo passwd -d ubuntu
sudo apt install -y certbot python3-certbot-nginx tigervnc-standalone-server nginx

# xstartup script for TigerVNCServer
sudo mkdir -p /home/ubuntu/.vnc
printf '
#!/bin/bash 
unset SESSION_MANAGER 
unset DBUS_SESSION_BUS_ADDRESS 
eval "$(dbus-launch --sh-syntax)" 
export XDG_RUNTIME_DIR="/run/user/$(id -u)" 
mkdir -p $XDG_RUNTIME_DIR 
chmod 0700 $XDG_RUNTIME_DIR 
export XAUTHORITY=$HOME/.Xauthority 
export DISPLAY=:1.0 
xrdb $HOME/.Xresources 
startxfce4
' | sudo tee /home/ubuntu/.vnc/xstartup > /dev/null
sudo chmod 777 /home/ubuntu/.vnc/xstartup


# hostname
sudo mkdir /miscFiles
sudo chown -R ubuntu:ubuntu /miscFiles
printf '이거' | sudo tee /miscFiles/hostname > /dev/null
sudo chmod 644 /miscFiles/hostname
printf '이메일주소' | sudo tee /miscFiles/email > /dev/null
sudo chmod 644 /miscFiles/email


# nginx config
printf '
upstream guacamole {
        server 127.0.0.1:8080;
}

server {
        listen 80 default_server;
        listen [::]:80 default_server;
        server_name guacamole_server;

        location / {                   
                proxy_pass http://guacamole;            
                proxy_set_header Upgrade $http_upgrade; 
                proxy_set_header Connection upgrade;    
                proxy_set_header Host $host;            
                proxy_set_header Accept-Encoding gzip;  
        }                                               
}
' | sudo tee /etc/nginx/sites-available/guacamole.conf > /dev/null
sudo chmod 0755 /etc/nginx/sites-available/guacamole.conf 


# apt mozilla preference file
# 
# apt에서 firefox 설치하면 snap을 이용하여 firefox를 설치하는데, 
# vnc세션에서는 기존의 snap으로 설치한 패키지가 실행되지 않음.
# 이를 우회하여 직접 .deb을 받아 설치할 수 있도록 함
printf '
Package: * 
Pin: release o=LP-PPA-mozillateam 
Pin-Priority: 900
' | sudo tee /etc/apt/preferences.d/mozilla > /dev/null
sudo chmod 0644 /etc/apt/preferences.d/mozilla

# apt jammy preference file
# ubuntu 24.04에서는 apt를 통해 tomcat9를 자동으로 설치할 수 없음(tomcat10 설치됨)
# apt pinning을 이용하여 tomcat9 설치하도록 지정
printf '
# allow only specific Tomcat9 from Jammy
Package: tomcat9 tomcat9-admin tomcat9-common tomcat9-user
Pin: release n=jammy
Pin-Priority: 1001' | sudo tee /etc/apt/preferences.d/jammy-tomcat > /dev/null
sudo chmod 0644 /etc/apt/preferences.d/jammy-tomcat

# 바탕 화면의 firefox 바로 가기 파일.
sudo mkdir -p /home/ubuntu/Desktop
printf '
[Desktop Entry]
Version=1.0
Type=Application
Name=Firefox Web Browser
Comment=Browse the World Wide Web
Exec=firefox %u
Icon=firefox
Path=
Terminal=false
StartupNotify=true
' | sudo tee /home/ubuntu/Desktop/Firefox.desktop > /dev/null
sudo chmod 0755 /home/ubuntu/Desktop/Firefox.desktop

# vnc접속 시(ubuntu 로그인 시)마다 필요한 스크립트 (자동 실행됨)
printf '
gnome-keyring-daemon -r -d
echo "ubuntu" | sudo apt update
killall firefox
sudo apt remove -y firefox
sudo snap remove firefox
sudo apt install -y firefox
' | sudo tee /miscFiles/chores.sh > /dev/null
sudo chmod 0755 /miscFiles/chores.sh

# 위의 스크립트 자동실행하는 런처 (~/.config/autostart)
sudo mkdir -p /home/ubuntu/.config/autostart
printf '
[Desktop Entry]
Encoding=UTF-8
Version=0.9.4
Type=Application
Name=startupChores
Comment=ibus&keyring&firefox update
Exec=/miscFiles/chores.sh
OnlyShowIn=XFCE;
RunHook=0
StartupNotify=false
Terminal=true
Hidden=false
' | sudo tee /home/ubuntu/.config/autostart/startupChores.desktop > /dev/null
sudo chmod 0664 /home/ubuntu/.config/autostart/startupChores.desktop
  


sudo install -d -m 0755 /etc/apt/keyrings
wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O- | \
  sudo tee /etc/apt/keyrings/packages.mozilla.org.asc >/dev/null
echo "deb [signed‑by=/etc/apt/keyrings/packages.mozilla.org.asc] \
  https://packages.mozilla.org/apt mozilla main" \
  | sudo tee -a /etc/apt/sources.list.d/mozilla.list > /dev/null

sudo mkdir -p /root/.gnupg
sudo chmod 700 /root/.gnupg


sudo gpg --no-default-keyring --keyring gnupg-ring:/etc/apt/trusted.gpg.d/mozilla.gpg --keyserver keyserver.ubuntu.com --recv-key C0BA5CE6DC6315A3
sudo chmod 644 /etc/apt/trusted.gpg.d/mozilla.gpg

# PPA 지정 후 apt 레포 업데이트.
sudo add-apt-repository ppa:mozillateam/ppa -y
sudo apt update
sudo apt-get update

# xfce desktop environment & display manager 설치
echo -e "\n\n\n\n\n\n\n" | sudo apt install -y xfce4 xfce4-goodies

# vscode 사용시 github 로그인을 위해 gnome-keyring 설치 및 실행
sudo apt install -y gnome-keyring
sudo apt install -y libsecret-1-0 libsecret-1-dev
gnome-keyring-daemon -r -d

# guacamole 설치
sudo apt install -y build-essential libcairo2-dev libjpeg-turbo8-dev \
    libpng-dev libtool-bin libossp-uuid-dev libvncserver-dev \
    freerdp2-dev libssh2-1-dev libtelnet-dev libwebsockets-dev \
    libpulse-dev libvorbis-dev libwebp-dev libssl-dev \
    libpango1.0-dev libswscale-dev libavcodec-dev libavutil-dev \
    libavformat-dev

sudo wget https://downloads.apache.org/guacamole/1.5.5/source/guacamole-server-1.5.5.tar.gz
sudo tar -xvf guacamole-server-1.5.5.tar.gz
cd guacamole-server-1.5.5
sudo ./configure --with-init-dir=/etc/init.d --enable-allow-freerdp-snapshots
sudo make
sudo make install

sudo ldconfig
sudo systemctl daemon-reload
sudo systemctl start guacd
sudo systemctl enable guacd
sudo mkdir -p /etc/guacamole/{extensions,lib}

sudo apt install tomcat9 tomcat9-admin tomcat9-common tomcat9-user -y
cd /home/ubuntu
sudo wget https://downloads.apache.org/guacamole/1.5.5/binary/guacamole-1.5.5.war
sudo mv guacamole-1.5.5.war /var/lib/tomcat9/webapps/guacamole.war
sudo systemctl start guacd tomcat9
sudo apt install mariadb-server -y

printf '1234\nn\nn\nn\nY\nY\nY\n' | sudo mysql_secure_installation

sudo wget https://dev.mysql.com/get/Downloads/Connector-J/mysql-connector-java-8.0.26.tar.gz
sudo tar -xf mysql-connector-java-8.0.26.tar.gz
sudo cp mysql-connector-java-8.0.26/mysql-connector-java-8.0.26.jar /etc/guacamole/lib/
sudo wget https://downloads.apache.org/guacamole/1.5.5/binary/guacamole-auth-jdbc-1.5.5.tar.gz

sudo tar -xf guacamole-auth-jdbc-1.5.5.tar.gz
sudo mv guacamole-auth-jdbc-1.5.5/mysql/guacamole-auth-jdbc-mysql-1.5.5.jar /etc/guacamole/extensions/



printf 'CREATE DATABASE guac_db;
CREATE USER "guac_user"@"localhost" IDENTIFIED BY "1234";
GRANT SELECT,INSERT,UPDATE,DELETE ON guac_db.* TO "guac_user"@"localhost";
FLUSH PRIVILEGES;
quit
' | sudo mysql -u root -p1234

cd guacamole-auth-jdbc-1.5.5/mysql/schema

sudo cat *.sql | sudo mysql -u root -p1234 guac_db
printf '
/* ------------------------------------------------------------------ */
/* 스크립트 실행 전:  실제 Guacamole 사용자명을 지정하세요            */
SET @GUAC_USER := "guacadmin";      -- READ 권한을 부여할 사용자(entity)  */

/* ------------------------------------------------------------------ */
/* 1) 커넥션 생성 (필드 대부분 NULL 처리)                             */
INSERT INTO guacamole_connection (
        connection_name, protocol,
        parent_id, max_connections, max_connections_per_user,
        proxy_hostname, proxy_port, proxy_encryption_method,
        connection_weight, failover_only
) VALUES (
        "connection",            -- 원하는 커넥션 이름
        "vnc",                   -- 프로토콜
        NULL, NULL, NULL,
        NULL, NULL, NULL,
        NULL, FALSE
);

/* 2) 방금 삽입된 커넥션 ID 확보 (MySQL 전역 함수) */
SET @CID := LAST_INSERT_ID();

/* ------------------------------------------------------------------ */
/* 3) 필수 VNC 매개변수 삽입                                          */
/*    hostname·port·username·password는 공식 매개변수 이름이다        */
/*    (문서 참고: hostname/port【158…†L755-L765】                     */
/*                 username/password【158…†L780-L788】)               */
INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)
VALUES
    (@CID, "hostname", "localhost"),
    (@CID, "port",     "5901"),
    (@CID, "username", "ubuntu"),
    (@CID, "password", "123456");

/* ------------------------------------------------------------------ */
/* 4) READ 권한을 줄 사용자의 entity_id 계산                          */
SELECT entity_id INTO @EID
FROM   guacamole_entity
WHERE  name = @GUAC_USER
  AND  type = "USER";            -- 그룹이면 "USER_GROUP"

/* 5) 커넥션 READ 권한 부여 (필요 시 UPDATE/DELETE/ADMINISTER 추가)   */
/*    권한 종류 설명: ADMINISTER/READ/UPDATE/DELETE【665…†L890-L896】 */
INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)
VALUES (@EID, @CID, "READ");
quit
' | sudo mysql -u root -p1234 guac_db


printf '
# MySQL properties
mysql-hostname: 127.0.0.1
mysql-port: 3306
mysql-database: guac_db
mysql-username: guac_user
mysql-password: 1234' | sudo tee -a /etc/guacamole/guacamole.properties > /dev/null





sudo systemctl restart tomcat9 guacd mysql

# nginx configuration 세팅 및 Https 연결을 위해 Certbot으로 SSL 인증서 설정
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/guacamole.conf /etc/nginx/sites-enabled/guacamole
sudo certbot -d $(sudo cat /miscFiles/hostname) --nginx --non-interactive --redirect --agree-tos -m $(sudo cat /miscFiles/email)
sudo systemctl restart nginx




# firefox 설치
## 얘도 안됨.. snap말고 mozilla 가져오는 법, cache 뒤져보기
## snap으로 설치되지만 실행은 잘 됨.
sudo apt install -y firefox

# vscode 설치
echo "code code/add-microsoft-repo boolean true" | sudo debconf-set-selections
sudo apt-get install -y wget gpg
cd /home/ubuntu
wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > microsoft.gpg
sudo install -D -o root -g root -m 644 microsoft.gpg /usr/share/keyrings/microsoft.gpg
rm -f microsoft.gpg

printf '
Types: deb
URIs: https://packages.microsoft.com/repos/code
Suites: stable
Components: main
Architectures: amd64,arm64,armhf
Signed-By: /usr/share/keyrings/microsoft.gpg
' | sudo tee /etc/apt/sources.list.d/vscode.sources

sudo apt install -y apt-transport-https
sudo apt update
sudo apt install -y code

# vscode 바탕 화면 바로가기
printf '
[Desktop Entry]
Name=Visual Studio Code
Comment=Code Editing. Redefined.
GenericName=Text Editor
Exec=/usr/share/code/code %F
Icon=vscode
Type=Application
StartupNotify=false
StartupWMClass=Code
Categories=TextEditor;Development;IDE;
MimeType=application/x-code-workspace;
Actions=new-empty-window;
Keywords=vscode;
Path=
Terminal=false

[Desktop Action new-empty-window]
Name=New Empty Window
Name[de]=Neues leeres Fenster
Name[es]=Nueva ventana vacía
Name[fr]=Nouvelle fenêtre vide
Name[it]=Nuova finestra vuota
Name[ja]=新しい空のウィンドウ
Name[ko]=새 빈 창
Name[ru]=Новое пустое окно
Name[zh_CN]=新建空窗口
Name[zh_TW]=開新空視窗
Exec=/usr/share/code/code --new-window %F
Icon=vscode
' | sudo tee /home/ubuntu/Desktop/code.desktop
sudo chmod 775 /home/ubuntu/Desktop/code.desktop

# ibus 입력기 & ibus-hangul 설치
sudo apt install ibus -y
sudo apt install ibus-hangul -y


# vncserver 열기
sudo chown -R ubuntu:ubuntu /home/ubuntu
cd /home/ubuntu
chmod -R 777 .vnc
echo -e "123456\n123456\nn\n" | tigervncserver -localhost :1 

# 한글 폰트 설치
sudo apt install fontconfig
curl -o nanumfont.zip http://cdn.naver.com/naver/NanumFont/fontfiles/NanumFont_TTF_ALL.zip
sudo unzip -d /usr/share/fonts/nanum nanumfont.zip
sudo rm nanumfont.zip
wget https://github.com/naver/d2codingfont/releases/download/VER1.3.2/D2Coding-Ver1.3.2-20180524.zip
sudo unzip -d /usr/share/fonts/d2coding D2Coding-Ver1.3.2-20180524.zip
sudo rm D2Coding-Ver1.3.2-20180524.zip
sudo fc-cache -f -v

# 80, 443 포트 열기
sudo iptables -I INPUT 1 -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 2 -p tcp --dport 443 -j ACCEPT
