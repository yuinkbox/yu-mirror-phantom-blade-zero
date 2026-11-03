"""Hidden UI smoke check for the standalone mod desktop app."""
import json
import mod_app


def main():
    app = mod_app.App(smoke=True)
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    try:
        app.update()
        assert app.state() == 'withdrawn'
        assert not app.worker.is_alive()
        assert not app.start_button.enabled
        assert not app.stop_button.enabled
        assert not app.install_button.enabled
        assert not app.rollback_button.enabled
        assert mod_app.VERSION in app.title()
        assert app.threshold.get() == 40
        assert app.combo_preset.get() == '均衡'
        assert app.blue_response.get().startswith('蓝光')
        return {'version': mod_app.VERSION, 'hidden': True, 'worker_started': False,
                'controls_disabled_until_connected': True, 'callback_errors': errors}
    finally:
        app.close()


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False))
