Браузер медиа для плагина Yandex Station
========================================
> Включайте музыку, плейлисты и радио на Яндекс.Станции из Home Assistant!
>
> [![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
> [![Лицензия](https://img.shields.io/badge/%D0%9B%D0%B8%D1%86%D0%B5%D0%BD%D0%B7%D0%B8%D1%8F-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
> [![Поддержка](https://img.shields.io/badge/%D0%9F%D0%BE%D0%B4%D0%B4%D0%B5%D1%80%D0%B6%D0%B8%D0%B2%D0%B0%D0%B5%D1%82%D1%81%D1%8F%3F-%D0%B4%D0%B0-green.svg)](https://github.com/alryaz/hass-lkcomu-interrao/graphs/commit-activity)
>
> [![Пожертвование Yandex](https://img.shields.io/badge/%D0%9F%D0%BE%D0%B6%D0%B5%D1%80%D1%82%D0%B2%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5-Yandex-red.svg)](https://money.yandex.ru/to/410012369233217)
> [![Пожертвование PayPal](https://img.shields.io/badge/%D0%9F%D0%BE%D0%B6%D0%B5%D1%80%D1%82%D0%B2%D0%BE%D0%B2%D0%B0%D0%BD%D0%B8%D0%B5-Paypal-blueviolet.svg)](https://www.paypal.me/alryaz)

## Скриншот
<details>
  <summary><b>Корневой раздел: Библиотека</b></summary>  
  <img src="https://raw.githubusercontent.com/alryaz/hass-yandex-music-browser/main/images/library.png" alt="Библиотека">
</details>
<details>
  <summary><b>Раздел: Жанры</b></summary>  
  <img src="https://raw.githubusercontent.com/alryaz/hass-yandex-music-browser/main/images/genres.png" alt="Жанры">
</details>
<details>
  <summary><b>Раздел: Новые релизы</b></summary>  
  <img src="https://raw.githubusercontent.com/alryaz/hass-yandex-music-browser/main/images/new_releases.png" alt="Новые релизы">
</details>

## Введение

Проект вырос из Pull-request-а: https://github.com/AlexxIT/YandexStation/pull/133.

## Установка

> ⚠️ Для работы компонента сперва потребуется установить: [AlexxIT/YandexStation](https://github.com/AlexxIT/YandexStation)
> и настроить авторизацию. Информация по установке и конфигурации доступна по ссылке.

### Установка посредством HACS
> 👍 ️Рекомендованный способ

1. Откройте HACS (через `Extensions` в боковой панели)
1. Добавьте новый произвольный репозиторий:
   1. Выберите `Integration` (`Интеграция`) в качестве типа репозитория
   1. Введите ссылку на репозиторий: `https://github.com/alryaz/hass-yandex-music-browser`
   1. Нажмите кнопку `Add` (`Добавить`)
   1. Дождитесь добавления репозитория (занимает до 10 секунд)
   1. Теперь вы должны видеть доступную интеграцию `Yandex Music Browser` (`Браузер Яндекс Музыки`) в списке новых интеграций.
1. Нажмите кнопку `Install` чтобы увидеть доступные версии
1. Установите последнюю версию нажатием кнопки `Install`
1. Перезапустите HomeAssistant

### Вручную
> ⚠️ Не рекомендуется

Клонируйте репозиторий во временный каталог, затем создайте каталог `custom_components` внутри папки конфигурации
вашего HomeAssistant (если она еще не существует). Затем переместите папку `yandex_music_browser` из папки `custom_components` 
репозитория в папку `custom_components` внутри папки конфигурации HomeAssistant.
Пример (при условии, что конфигурация HomeAssistant доступна по адресу `/mnt/homeassistant/config`) для Unix-систем:
```
git clone https://github.com/alryaz/hass-yandex-music-browser.git hass-yandex-music-browser
mkdir -p /mnt/homeassistant/config/custom_components
mv hass-yandex-music-browser/custom_components/yandex_music_browser /mnt/homeassistant/config/custom_components/
```

## Конфигурация

### Из меню _Интеграции_

1. Найдите интеграцию `Yandex Music Browser` (`Браузер Яндекс Музыки`) в списке интеграций
1. Нажмите на найденную интеграцию
1. Следуйте инструкциям на экране для завершения настройки

### Используя `configuration.yaml`

1. Добавьте `yandex_music_browser:` куда-нибудь в Ваш файл `configuration.yaml` (двоеточие на конце обязательно!)
1. Перезапустите Home Assistant

## Что поддерживается

Проект поддерживает проигрывание почти всех типов медиа, которые получаемы библиотекой `yandex-music`.

### В локальном режиме

> @TODO@

### В облачном режиме

> @TODO@
