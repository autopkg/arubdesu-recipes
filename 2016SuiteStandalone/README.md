This will help you download the latest standalone installer for an Office App.

It will:
- create an Extended Attribute that will check each Office App version.
- create a Smart Group that includes computers that don't have this version installed
- create a script that will trigger the policy with the Serializer
- create a Self-Service policy, featured on the main page.

If you need to serialize your VL installers:
- put your `Microsoft_Office_2016_VL_Serializer.pkg` in payload/
- make sure `manual_trigger_office.sh` is included in your Office App recipes
- customize `TRIGGER_OTHER` in MSO2016License.jss.recipe


