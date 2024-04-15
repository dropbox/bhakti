rule KerasRequests
{
    meta:
        author         = "Dropbox Threat Intel"
        description    = "This signature fires on the presence of Base64 encoded URI prefixes (http:// and https://) within a lambda layer of a keras Tensorflow model. The simple presence of such strings is not inherently an indicator of malicious content, but is worth further investigation."
        created_date   = "2024-04-05"
        updated_date   = "2024-04-05"
    strings: 
        $function = "function_type"
        $layer = "lambda" 
        $req = "requests" base64
    
    condition:
        $req and ($function and $layer)
}