# Icinga Image Builder

Tool độc lập để build và push image cho Icinga, theo cách vận hành gần với `kolla-build`:

- version của từng thành phần nằm trong một file YAML;
- chọn build toàn bộ hoặc một/nhiều image theo cú pháp như `kolla-build`;
- pull base image từ Docker Hub hoặc registry/mirror do người vận hành chỉ định;
- tag local hoặc push lên Harbor/registry riêng;
- mặc định giữ nguyên image theo version đã pin; chỉ custom khi khai Dockerfile riêng;
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
tag_suffix: ""                    # mặc định tag đúng version đã pin
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

Build đủ năm image đúng version đã pin:

```bash
icinga-image-build build --config ./config.yml --pull
```

Chỉ build Icinga 2, theo cú pháp positional giống Kolla build:

```bash
icinga-image-build build icinga2 --config ./config.yml --pull
```

Nếu Docker báo `network bridge not found`, dùng network host của build node:

```bash
icinga-image-build build icinga2 --config ./config.yml --pull --network host
```

Chọn nhiều image bằng cách ghi tên liên tiếp:

```bash
icinga-image-build build icinga2 icingadb icingaweb2 --config ./config.yml
```

Option cũ vẫn được hỗ trợ để tương thích:

```bash
icinga-image-build build --config ./config.yml --image icinga2,icingadb
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

## 4. Image mặc định và custom image

Nếu không khai `dockerfile`, cả năm image giữ nguyên nội dung upstream và dùng đúng version trong
`versions`. Ví dụ `icinga2` được build/tag thành `icinga2:2.16.5`, không tự cài thêm package và
không tự thêm suffix.

`versions` trong `config.yml` là nguồn duy nhất của version base image. Dockerfile built-in không chứa
version cố định; tool ghép `source_registry + source_repository + version` rồi bắt buộc truyền kết quả
qua build argument `BASE_IMAGE`. Không được khai `BASE_IMAGE` trong `images.<name>.build_args`.

Khi cần custom, tạo Dockerfile riêng. Tool luôn truyền `BASE_IMAGE` theo version đã chọn. Project có
sẵn file mẫu `examples/icinga2-openstack/Dockerfile`; có thể copy rồi sửa, hoặc trỏ thẳng tới file
đó. Nội dung mẫu cài Python và OpenStack SDK:

```dockerfile
ARG BASE_IMAGE=scratch
FROM ${BASE_IMAGE}

ARG OPENSTACKSDK_VERSION=4.21.0
ARG PYYAML_VERSION=6.0.3

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv \
    && python3 -m venv /opt/icinga-venv \
    && /opt/icinga-venv/bin/pip install --no-cache-dir \
       "openstacksdk==${OPENSTACKSDK_VERSION}" "PyYAML==${PYYAML_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

USER icinga
```

Trỏ tới Dockerfile và context riêng, đường dẫn tương đối được tính từ thư mục chứa `config.yml`:

```yaml
images:
  icinga2:
    dockerfile: "examples/icinga2-openstack/Dockerfile"
    context: "examples/icinga2-openstack"
    tag_suffix: "-custom"
    build_args:
      OPENSTACKSDK_VERSION: "4.21.0"
      PYYAML_VERSION: "6.0.3"
```

Nếu muốn image custom thay thế trực tiếp tag đã pin trong registry riêng, bỏ `tag_suffix`. Nếu muốn
giữ đồng thời bản nguyên gốc và bản custom, dùng `tag_suffix: "-custom"` như ví dụ trên.

Có thể chạy ngay ví dụ đi kèm:

```bash
icinga-image-build build icinga2 \
  --config ./examples/icinga2-openstack/config.yml
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
  icinga2: icinga2:2.16.5
  icingadb: icingadb:1.5.1
  icingadb_redis: icingadb-redis:7.4.11
  icingaweb2: icingaweb2:2.14.0
  mariadb: mariadb:12.3.3
```

Merge hai key này vào `/etc/icinga-ansible/globals.yml`, sau đó chạy `icinga-ansible pull`,
`prechecks` và `deploy`/`upgrade` theo runbook của project đó.

## 6. Kiểm tra image đã push

```bash
docker manifest inspect 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5 >/dev/null
docker pull 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5
docker image inspect 172.28.81.109/openstack.kolla/icinga/icinga2:2.16.5 \
  --format '{{ index .Config.Labels "org.opencontainers.image.base.name" }}'
```

Với image Icinga 2 custom có suffix, kiểm tra package:

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
- Chỉ các image được chọn theo positional hoặc `--image` mới xuất hiện trong file override.
