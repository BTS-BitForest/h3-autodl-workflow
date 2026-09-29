# H3 SSH networking: loopback router chooses the route per destination.
if [ -n "${SSH_CONNECTION:-}" ]; then
    /opt/h3-python/bin/python /opt/h3-suite/network/ensure.py >/dev/null 2>&1
    export HTTP_PROXY=http://127.0.0.1:41080 HTTPS_PROXY=http://127.0.0.1:41080 ALL_PROXY=http://127.0.0.1:41080
    export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" all_proxy="$ALL_PROXY"
    case ",${NO_PROXY:-}," in
      *,api.deepseek.com,*) ;;
      *) export NO_PROXY="localhost,127.0.0.1,::1,api.deepseek.com,.deepseek.com${NO_PROXY:+,$NO_PROXY}" ;;
    esac
    export no_proxy="$NO_PROXY"
fi
