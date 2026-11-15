"""Persistent sensor mounting and joint ranges; live world references are never restored."""
import json
from wearable_test.model import ArmModel


def config_status(path,state,detail):
    return dict(path=str(path.resolve()),state=state,detail=detail)


def load_model(settings_path,calibration_path,demo=False):
    model = None
    try:
        settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else None
        if settings is not None: model = ArmModel(settings)
        record = json.loads(calibration_path.read_text(encoding='utf-8')) if not demo and calibration_path.exists() else None
        # The calibration file is self-contained; explicit dimension/axis edits still take precedence.
        if model is None: model = ArmModel(record['settings'] if record else {})
        if record is not None:
            model.restore_mounting(record)
            return model,config_status(calibration_path,'loaded','Mounting and joint ranges loaded. Recenter in pose A; no A/B or range recapture needed unless a strap moved.')
        return model,config_status(calibration_path,'demo' if demo else 'none',
                                  'Demo does not overwrite your saved hardware calibration.' if demo else 'A/B and each joint-range capture are saved automatically here.')
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
        return model or ArmModel(),config_status(calibration_path,'error',f'Saved configuration unavailable: {error}. Review dimensions/axes and capture A/B; the existing file was kept.')


def save_model(model,path):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(model.mounting_record(),indent=2,allow_nan=False)+'\n',encoding='utf-8')
    temporary.replace(path)
    return config_status(path,'saved','Mounting, dimensions and learned joint ranges saved. After a restart, only recenter in pose A.')
