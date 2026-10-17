# Icinga Image Builder

Tool độc lập để build và push image cho Icinga, theo cách vận hành gần với `kolla-build`:

- version của từng thành phần nằm trong một file YAML;
- chọn build toàn bộ hoặc từng image;
- pull base image từ Docker Hub hoặc registry/mirror do người vận hành chỉ định;
- tag local hoặc push lên Harbor/registry riêng;
- dùng Dockerfile riêng và build argument riêng cho từng image;
- tự chuyển proxy đang có trong environment của build node vào `docker build`;
- xuất YAML `custom_image_registry` và `custom_image_overrides` để đưa vào `icinga-ansible`.

Project này không import code, không đọc mặc định `/etc/icinga-ansible`, và không cần cài chung với
`icinga-ansible`. Hai project chỉ giao tiếp qua tên/tag image trong registry.

## 1. Cài trong virtual environment

Trên build node đã có Docker:

```bash
sudo apt install -y python3-venv
python3 -m venv /path/to/venv
source /path/to/venv/bin/activate
python -m pip install -U pip
python -m pip install -e /path/to/icinga-image-builder
icinga-image-build --help
```

`pip install -e` phù hợp khi bạn muốn sửa Dockerfile trong source tree. Có thể dùng `pip install
/path/to/icinga-image-builder` nếu chỉ dùng Dockerfile đóng gói sẵn.

Docker chạy bằng user hiện tại. Nếu Docker socket chỉ cho root, thêm user build vào group `docker`,
đăng nhập lại và kiểm tra `docker info`. Không chạy cả virtual environment bằng `sudo`.

## 2. Cấu hình version và registry

Sửa `config.yml`:

```yaml
versions:
  icinga2: "2.16.5"
  icingadb: "1.5.1"
  icingadb_redis: "7.4.11"
  icingaweb2: "2.14.0"
  mariadb: "12.3.3"

source_registry: ""
target_registry: "172.28.81.109/openstack.kolla/icinga"
local_namespace: "icinga-local"
tag_suffix: "-custom"
platform: "linux/amd64"
build_network: ""                  # đặt "host" nếu build node không có Docker bridge
retries: 1                         # retry thêm một lần khi Docker/registry lỗi tạm thời
```

`source_registry` và `target_registry` chỉ chứa `host[:port][/project]`, không có `https://`.

- `source_registry: ""`: base image lấy từ Docker Hub.
- `source_registry: "harbor.example.com/dockerhub"`: base image Icinga 2 trở thành
  `harbor.example.com/dockerhub/icinga/icinga2:2.16.5`. Project Harbor này phải là proxy cache
  hoặc đã chứa đúng repository nguồn.
- `target_registry`: nơi nhận image custom khi push. Project phải tồn tại và tài khoản đã có quyền.
- Nếu để trống `target_registry`, build vẫn chạy và tag dưới `local_namespace`; command `push` sẽ
  từ chối để tránh đẩy nhầm nơi.

Kiểm tra trước mọi thay đổi:

```bash
icinga-image-build plan --config ./config.yml
```

Có thể ghi đè registry tại command line mà không sửa file:

```bash
icinga-image-build plan --config ./config.yml \
  --source-registry harbor.example.com/dockerhub \
  --registry harbor.example.com/icinga
```

## 3. Build

Build đủ năm image:

```bash
icinga-image-build build --config ./config.yml --pull
```

Chỉ build Icinga 2:

```bash
icinga-image-build build --config ./config.yml --image icinga2 --pull
```

Nếu Docker báo `network bridge not found`, dùng network host của build node:

```bash
icinga-image-build build --config ./config.yml --image icinga2 --pull --network host
```

Chọn nhiều image bằng cách lặp option hoặc phân cách bằng dấu phẩy:

```bash
icinga-image-build build --config ./config.yml \
  --image icinga2,icingadb --image icingaweb2
```

Build không cache và push ngay:

```bash
docker login 172.28.81.109
icinga-image-build build --config ./config.yml --pull --no-cache --push
```

Tool không nhận password registry trên command line. Nó dùng credential store của Docker sau
`docker login`, tránh để mật khẩu trong shell history hoặc process list.

Push các image đã build từ trước:

```bash
icinga-image-build push --config ./config.yml
```

## 4. Custom image

Dockerfile mặc định của `icinga2` cài sẵn:

- `python3` và `python3-venv`;
- virtual environment `/opt/icinga-venv`;
- `openstacksdk` và `PyYAML`.

Hai package Python được pin theo `build_args` trong `config.yml`. Hãy đổi version ở đó khi muốn
nâng package; cách này giúp cùng một config tạo lại cùng dependency thay vì âm thầm lấy bản mới.

Các Dockerfile còn lại giữ nguyên base image và là điểm bắt đầu để thêm package. Tool luôn truyền
`BASE_IMAGE` theo version đã chọn. Dockerfile custom phải bắt đầu như sau:

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
```

Trỏ tới Dockerfile và context riêng, đường dẫn tương đối được tính từ thư mục chứa `config.yml`:

```yaml
images:
  icinga2:
    dockerfile: "custom/icinga2/Dockerfile"
    context: "custom/icinga2"
    build_args:
      OPENSTACKSDK_VERSION: "4.7.1"
```

Tool truyền `http_proxy`, `https_proxy`, `no_proxy` cùng các biến viết hoa từ environment hiện tại.
Nếu build node dùng `/etc/environment`, đăng nhập lại hoặc nạp các biến đó vào shell trước khi build.

## 5. Dùng image với icinga-ansible

Xuất file riêng khi lập kế hoạch hoặc build:

```bash
icinga-image-build plan --config ./config.yml \
  --output-overrides ./icinga-ansible-images.yml
```

Nội dung sinh ra có dạng:

```yaml
custom_image_registry: 172.28.81.109/openstack.kolla/icinga
custom_image_overrides:
  icinga2: icinga2:2.16.5-custom
  icingadb: icingadb:1.5.1-custom
  icingadb_redis: icingadb-redis:7.4.11-custom
  icingaweb2: icingaweb2:2.14.0-custom
  mariadb: mariadb:12.3.3-custom
```

Merge hai key này vào `/etc/icinga-ansible/globals.yml`, sau đó chạy `icinga-ansible pull`,
`prechecks` và `deploy`/`upgrade` theo runbook của project đó.

## 6. Kiểm tra image đã push

```bash
docker manifest inspect 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5-custom >/dev/null
docker pull 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5-custom
docker image inspect 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5-custom \
  --format '{{ index .Config.Labels "org.opencontainers.image.base.name" }}'
```

Với image Icinga 2 mẫu, kiểm tra package custom:

```bash
docker run --rm --entrypoint /opt/icinga-venv/bin/python \
  172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5-custom \
  -c 'import openstack, yaml; print(openstack.__version__)'
```

## 7. Quy tắc an toàn

- Mọi version phải được khai rõ theo dạng `x.y.z`; tool không tự dùng `latest`.
- `plan` không gọi Docker và không thay đổi máy.
- `build` không tự push nếu thiếu `--push`.
- `push` bị chặn khi chưa có target registry.
- Build lỗi ở image nào thì dừng ngay ở image đó.
- Chỉ các image được chọn bằng `--image` mới xuất hiện trong file override.
