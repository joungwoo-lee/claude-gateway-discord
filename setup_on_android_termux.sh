#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

TERMUX_PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/5] Termux 기본 시스템 업데이트 및 설치..."
pkg update -y && pkg upgrade -y
pkg install proot-distro -y

echo "[2/5] 우분투(Ubuntu) 환경 설치..."
proot-distro list | grep -q "installed" || proot-distro install ubuntu

echo "[3/5] 사용자 생성 및 개발 환경 구축..."
proot-distro login ubuntu -- bash -c "
  set -euo pipefail
  apt update && apt install -y sudo curl git python3 python3-pip python3-venv

  # claudegateway 사용자 생성 및 sudo 권한 부여
  if ! id 'claudegateway' &>/dev/null; then
    adduser --disabled-password --gecos '' claudegateway
    usermod -aG sudo claudegateway
    echo 'claudegateway ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
  fi

  # claudegateway 계정으로 작업 수행
  sudo -u claudegateway bash -c '
    set -euo pipefail

    # 1. Claude Code 설치
    curl -fsSL https://claude.ai/install.sh | bash
    echo "export PATH=\\$HOME/.local/bin:\\$PATH" >> ~/.bashrc

    # 2. 이 스크립트와 같은 폴더의 setup.sh 실행
    PROJECT_DIR=\""$TERMUX_PROJECT_DIR"\"
    if [ -f "\$PROJECT_DIR/setup.sh" ]; then
      cd "\$PROJECT_DIR"
      chmod +x setup.sh
      ./setup.sh
    else
      echo "❌ setup.sh not found in: \$PROJECT_DIR"
      exit 1
    fi
  '
"

echo "[4/5] Termux 시작 시 자동 로그인 설정..."
sed -i '/proot-distro login ubuntu/d' ~/.bashrc
echo "proot-distro login ubuntu --user claudegateway" >> ~/.bashrc

echo "------------------------------------------------"
echo "✅ 모든 설정과 프로젝트 셋업이 완료되었습니다!"
echo "1. Termux를 완전히 종료(상단 알림창 Exit) 후 다시 켜세요."
echo "2. 자동으로 'claudegateway' 계정으로 접속됩니다."
echo "3. 프로젝트 폴더: $TERMUX_PROJECT_DIR"
echo "------------------------------------------------"
