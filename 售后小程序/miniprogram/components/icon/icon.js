// Iconify icon name mapping
// Reference: https://icon-sets.iconify.design/
const ICON_MAP = {
  // navigation
  'plus': 'mdi:plus-circle-outline',
  'plus-fill': 'mdi:plus-circle',
  'dashboard': 'mdi:view-dashboard-outline',
  'dashboard-fill': 'mdi:view-dashboard',
  'engineer': 'mdi:account-hard-hat-outline',
  'engineer-fill': 'mdi:account-hard-hat',
  'user': 'mdi:account-circle-outline',
  'user-fill': 'mdi:account-circle',
  'task': 'mdi:clipboard-check-outline',
  'task-fill': 'mdi:clipboard-check',
  'history': 'mdi:clipboard-text-outline',
  'history-fill': 'mdi:clipboard-text',
  'settings': 'mdi:cog-outline',
  'settings-fill': 'mdi:cog',
  'camera': 'mdi:camera-outline',
  'camera-fill': 'mdi:camera',
  'location': 'mdi:map-marker-outline',
  'location-fill': 'mdi:map-marker',
  'arrow-left': 'mdi:arrow-left',
  'arrow-right': 'mdi:arrow-right',
  'back': 'mdi:chevron-left',
  'map': 'mdi:map',
  'upload': 'mdi:tray-arrow-up'
};

Component({
  properties: {
    name: {
      type: String,
      value: ''
    },
    size: {
      type: Number,
      value: 44
    },
    color: {
      type: String,
      value: ''
    }
  },

  data: {
    iconUrl: '',
    iconSize: 44
  },

  observers: {
    'name,size,color'(name, size, color) {
      const iconName = ICON_MAP[name] || `mdi:${name}`;
      let params = [];
      if (color) {
        params.push('color=' + encodeURIComponent(color.replace('#', '%23')));
      }
      const query = params.length ? '?' + params.join('&') : '';
      const url = `https://api.iconify.design/${iconName}.svg${query}`;
      this.setData({
        iconUrl: url,
        iconSize: size
      });
    }
  }
});
