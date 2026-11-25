include $(TOPDIR)/rules.mk

PKG_NAME:=deco-x55-defaults
PKG_VERSION:=2
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKG_MAINTAINER:=Andrei Vladescu

include $(INCLUDE_DIR)/package.mk

define Package/deco-x55-defaults
  SECTION:=base
  CATEGORY:=Base system
  TITLE:=Default settings for the TP-Link Deco X55 v3 community build
  PKGARCH:=all
endef

define Package/deco-x55-defaults/description
  First-boot defaults: root password (only if none is set), Wi-Fi like the stock firmware
  (5 GHz HE160 ch 44, 2.4 GHz HE40, country from factory_data, one SSID on both bands,
  802.11k/v, PMF), hardware flow offloading and WED. Never overrides existing settings.
endef

define Build/Compile
endef

define Package/deco-x55-defaults/install
	$(CP) ./files/* $(1)/
endef

$(eval $(call BuildPackage,deco-x55-defaults))
