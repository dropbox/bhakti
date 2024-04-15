rule KerasLambda
{
    meta:
        author         = "Dropbox Threat Intel"
        description    = "This signature fires on the presence of a lambda layer in a keras Tensorflow model. The simple presence of such a layer is not an indicator of malicious content, but is worth further investigation."
        created_date   = "2024-04-05"
        updated_date   = "2024-04-05"

    strings:
        $function = "function_type"
        $layer = "lambda" 

    condition:
        $function and $layer
}