#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

TERMUX_PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UBUNTU_MOUNT_DIR="/mnt/termux-project"

echo "[1/5] Termux 기본 시스템 업데이트 및 설치..."
pkg update -y && pkg upgrade -y
pkg install proot-distro -y

echo "[2/5] 우분투(Ubuntu) 환경 설치..."
proot-distro list | grep -q "installed" || proot-distro install ubuntu

echo "[3/5] 사용자 생성 및 개발 환경 구축..."
proot-distro login ubuntu --bind "$TERMUX_PROJECT_DIR:$UBUNTU_MOUNT_DIR" -- bash -s -- "$UBUNTU_MOUNT_DIR" <<'UBUNTU_SCRIPT'
set -euo pipefail

MOUNT_DIR="$1"
apt update && apt install -y sudo curl git python3 python3-pip python3-venv

# claudegateway 사용자 생성 및 sudo 권한 부여
if ! id 'claudegateway' &>/dev/null; then
  adduser --disabled-password --gecos '' claudegateway
  usermod -aG sudo claudegateway
  echo 'claudegateway ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
fi

# claudegateway 계정으로 작업 수행
sudo -u claudegateway env UBUNTU_MOUNT_DIR="$MOUNT_DIR" bash -s <<'USER_SCRIPT'
set -euo pipefail

# 1. Claude Code 설치
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# 2. Termux에서 받은 프로젝트를 Ubuntu 홈으로 복사 후 setup.sh 실행
PROJECT_DIR="$HOME/claude-gateway-discord"
mkdir -p "$PROJECT_DIR"
cp -a "$UBUNTU_MOUNT_DIR"/. "$PROJECT_DIR"/

if [ -f "$PROJECT_DIR/setup.sh" ]; then
  cd "$PROJECT_DIR"
  chmod +x setup.sh
  ./setup.sh
else
  echo "❌ setup.sh not found in: $PROJECT_DIR"
  exit 1
fi
USER_SCRIPT
UBUNTU_SCRIPT

echo "[4/5] Termux 시작 시 자동 로그인 설정..."
mkdir -p "$HOME/.termux"
AUTO_LOGIN_FILE="$HOME/.termux/auto_ubuntu_login.sh"
cat > "$AUTO_LOGIN_FILE" <<'AUTOLOGIN'
# added by setup_on_android_termux.sh
# run only on Termux host shell, avoid recursion inside Ubuntu session
if [ -n "${TERMUX_VERSION:-}" ] && [ -z "${CLAUDEGW_AUTOLOGIN_DONE:-}" ]; then
  export CLAUDEGW_AUTOLOGIN_DONE=1
  exec proot-distro login ubuntu --user claudegateway
fi
AUTOLOGIN
chmod +x "$AUTO_LOGIN_FILE"

for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
  touch "$rc"
  sed -i '/auto_ubuntu_login.sh/d' "$rc"
  printf '
# added by setup_on_android_termux.sh
[ -f "$HOME/.termux/auto_ubuntu_login.sh" ] && . "$HOME/.termux/auto_ubuntu_login.sh"
' >> "$rc"
done

echo "[5/5] 완료"
echo "------------------------------------------------"
echo "✅ 모든 설정과 프로젝트 셋업이 완료되었습니다!"
echo "1. Termux를 완전히 종료(상단 알림창 Exit) 후 다시 켜세요."
echo "2. 자동으로 'claudegateway' 계정으로 접속됩니다."
echo "3. Ubuntu 프로젝트 폴더: ~/claude-gateway-discord"
echo "------------------------------------------------"
