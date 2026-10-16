# SPDX-License-Identifier: Apache-2.0
# Works during product bootstrap, before my-dir exists.
FLORAL_GCAM_PATH := $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))
ifeq ($(wildcard $(FLORAL_GCAM_PATH)/generated/profile.json),)
$(error Run $(FLORAL_GCAM_PATH)/tools/fetch_prebuilts.py and tools/prepare.py before building)
endif
FLORAL_GCAM_READY := $(shell python3 "$(FLORAL_GCAM_PATH)/tools/verify_ready.py" 2>&1)
ifneq ($(FLORAL_GCAM_READY),ready)
$(error $(FLORAL_GCAM_READY))
endif
include $(FLORAL_GCAM_PATH)/generated/config.mk

ifneq ($(strip $(PRODUCT_DEVICE)),)
ifneq ($(PRODUCT_DEVICE),$(FLORAL_GCAM_DEVICE))
$(error Floral GCam was prepared for $(FLORAL_GCAM_DEVICE), but PRODUCT_DEVICE is $(PRODUCT_DEVICE))
endif
endif
ifneq ($(strip $(TARGET_ARCH)),)
ifneq ($(TARGET_ARCH),arm64)
$(error Floral GCam requires an arm64 Floral target)
endif
endif

PRODUCT_SOONG_NAMESPACES += $(FLORAL_GCAM_PATH) $(FLORAL_GCAM_VENDOR_NAMESPACES)
# The Soong overrides also cover packages inherited through other products.
PRODUCT_PACKAGES := $(filter-out $(FLORAL_GCAM_REPLACED_MODULES),$(PRODUCT_PACKAGES))
PRODUCT_PACKAGES += FloralGoogleCamera $(FLORAL_GCAM_DEPENDENCY_MODULES)
PRODUCT_COPY_FILES += \
    $(FLORAL_GCAM_PATH)/configs/default-permissions.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/default-permissions/default-permissions-floral-gcam.xml \
    $(FLORAL_GCAM_PATH)/configs/hidden-api.xml:$(TARGET_COPY_OUT_PRODUCT)/etc/sysconfig/floral-gcam-hidden-api.xml
