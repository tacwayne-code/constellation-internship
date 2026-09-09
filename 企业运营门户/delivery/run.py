import os
import uvicorn
from .app import create_app

if __name__=='__main__':
    host=os.environ.get('DELIVERY_HOST','127.0.0.1')
    if host not in ('127.0.0.1','::1','localhost') and (not os.environ.get('DELIVERY_ALLOWED_HOSTS') or os.environ.get('DELIVERY_SECURE_COOKIE')!='1'):
        raise RuntimeError('非本机开放需要配置允许的主机名和HTTPS安全Cookie')
    proxy_ips=os.environ.get('DELIVERY_PROXY_IPS','')
    if '*' in proxy_ips:
        raise RuntimeError('代理来源必须是明确的IP地址，不能使用通配符')
    uvicorn.run(create_app(),host=host,port=int(os.environ.get('DELIVERY_PORT','8782')),
                proxy_headers=bool(proxy_ips),forwarded_allow_ips=proxy_ips or '127.0.0.1')
