#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

echo "[1/4] Termux 기본 시스템 업데이트 및 설치..."
pkg update -y && pkg upgrade -y
pkg install proot-distro -y

echo "[2/4] 우분투(Ubuntu) 환경 설치..."
proot-distro list | grep -q "installed" || proot-distro install ubuntu

echo "[3/4] claudegateway 사용자 생성 및 Claude Code CLI 설치..."
proot-distro login ubuntu -- bash -s <<'UBUNTU_SCRIPT'
set -euo pipefail

apt update && apt install -y sudo curl git python3 python3-pip python3-venv

if ! id 'claudegateway' &>/dev/null; then
  adduser --disabled-password --gecos '' claudegateway
  usermod -aG sudo claudegateway
  echo 'claudegateway ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
fi

sudo -u claudegateway bash -s <<'USER_SCRIPT'
set -euo pipefail

curl -fsSL https://claude.ai/install.sh | bash
if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc; then
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
fi
USER_SCRIPT
UBUNTU_SCRIPT

echo "[4/4] Termux 시작 시 Ubuntu 자동 로그인 설정..."
mkdir -p "$HOME/.termux"
AUTO_LOGIN_FILE="$HOME/.termux/auto_ubuntu_login.sh"
cat > "$AUTO_LOGIN_FILE" <<'AUTOLOGIN'
# added by install_claudecodeCLI_on_android_termux.sh
if [ -n "${TERMUX_VERSION:-}" ] && [ -z "${CLAUDEGW_AUTOLOGIN_DONE:-}" ]; then
  export CLAUDEGW_AUTOLOGIN_DONE=1
  exec proot-distro login ubuntu --user claudegateway
fi
AUTOLOGIN
chmod +x "$AUTO_LOGIN_FILE"

for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
  touch "$rc"
  sed -i '/auto_ubuntu_login.sh/d' "$rc"
  printf '\n# added by install_claudecodeCLI_on_android_termux.sh\n[ -f "$HOME/.termux/auto_ubuntu_login.sh" ] && . "$HOME/.termux/auto_ubuntu_login.sh"\n' >> "$rc"
done

echo "------------------------------------------------"
echo "✅ Claude Code CLI Android(Termux) 설치가 완료되었습니다!"
echo "1. Termux를 완전히 종료(상단 알림창 Exit) 후 다시 켜세요."
echo "2. 자동으로 Ubuntu 'claudegateway' 계정으로 접속됩니다."
echo "3. 'claude' 명령으로 로그인 후 사용하세요."
echo "------------------------------------------------"
